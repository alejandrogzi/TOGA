"""This module contains functions to ensure TOGA arguments correctness."""
import os
import shutil
from itertools import islice
from twobitreader import TwoBitFile

from constants import Constants
from modules.common import to_log, call_process
from modules.common import read_isoforms_file

__author__ = "Bogdan M. Kirilenko, 2024"
__github__ = "https://github.com/kirilenkobm"


SANITY_CHECKER_PREFIX = "SANITY_CHECKER"
U12_FILE_COLS = 3
U12_AD_FIELD = {"A", "D"}


class TogaSanityChecker:
    """Utility class to ensure TOGA arguments correctness."""
    @staticmethod
    def check_dir_args_safety(toga_cls, location):
        protected_dirs = {os.path.abspath(x) for x in (location, toga_cls.wd, os.getcwd())}
        nd_dir_abspath = os.path.abspath(toga_cls.nextflow_dir)
        if nd_dir_abspath in protected_dirs:
            msg = (
                f"{SANITY_CHECKER_PREFIX}: "
                f"Error! Nextflow directory is set to {toga_cls.nextflow_dir}. "
                f"This directory is to be deleted after the TOGA pipeline execution. "
                f"However, it matches one of the following dirs:\n{protected_dirs}\n"
                f"Which must be preserved. Please, reassign the --nd argument."
            )
            to_log(msg)
            toga_cls.die()
        # TODO: consider other dangerous scenario
        to_log("Does it work?")
        return

    @staticmethod
    def check_args_correctness(toga_cls, args):
        """Check that arguments are correct.

        Error exit if any argument is wrong.
        """
        if args.cesar_buckets:
            # if set, need to check that it could be split into numbers
            comma_sep = args.cesar_buckets.split(",")
            all_numeric = [x.isnumeric() for x in comma_sep]
            if any(x is False for x in all_numeric):
                # there is some non-numeric value
                err_msg = (
                    f"Error! --cesar_buckets value {args.cesar_buckets} is incorrect\n"
                    f"Expected comma-separated list of integers"
                )
                toga_cls.die(err_msg)
        if not os.path.isfile(args.chain_input):
            toga_cls.die(f"Error! Chain file {args.chain_input} does not exist!")
        if not os.path.isfile(args.bed_input):
            toga_cls.die(f"Error! Bed file {args.bed_input} does not exist!")
        # TODO: consider other scenario

    @staticmethod
    def check_2bit_file_completeness(two_bit_file, chroms_sizes, chrom_file):
        """Check that 2bit file is readable."""
        try:  # try to catch EOFError: if 2bitreader cannot read file
            two_bit_reader = TwoBitFile(two_bit_file)
            # check what sequences are in the file:
            twobit_seq_to_size = two_bit_reader.sequence_sizes()
            twobit_sequences = set(twobit_seq_to_size.keys())
            to_log(f"Found {len(twobit_sequences)} sequences in {two_bit_file}")
        except EOFError as err:  # this is a file but twobit reader couldn't read it
            raise ValueError(str(err))
        # another check: that bed or chain chromosomes intersect 2bit file sequences
        check_chroms = set(chroms_sizes.keys())  # chroms in the input file
        intersection = twobit_sequences.intersection(check_chroms)
        chroms_not_in_2bit = check_chroms.difference(twobit_sequences)

        if len(chroms_not_in_2bit) > 0:
            missing_top_100 = list(chroms_not_in_2bit)[:100]
            missing_str = "\n".join(missing_top_100)
            err = (
                f"Error! 2bit file: {two_bit_file}; chain/bed file: {chrom_file}; "
                f"Some chromosomes present in the chain/bed file are not found in the "
                f"Two bit file. First <=100: {missing_str}"
            )
            raise ValueError(err)
        # check that sizes also match
        for chrom in intersection:
            twobit_seq_len = twobit_seq_to_size[chrom]
            comp_file_seq_len = chroms_sizes[chrom]
            # if None: this is from bed file: cannot compare
            if comp_file_seq_len is None:
                continue
            if twobit_seq_len == comp_file_seq_len:
                continue
            # got different sequence length in chain and 2bit files
            # which means these chains come from something different
            err = (
                f"Error! 2bit file: {two_bit_file}; chain_file: {chrom_file} "
                f"Chromosome: {chrom}; Sizes don't match! "
                f"Size in twobit: {twobit_seq_len}; size in chain: {comp_file_seq_len}"
            )
            to_log(err)
            raise ValueError(err)

    @staticmethod
    def check_and_write_u12_file(u12_arg, t_in_bed, temp_wd):
        """Sanity check for U12 file."""
        if u12_arg is None:
            return None
        # U12 file provided
        u12_saved_file = os.path.join(temp_wd, "u12_data.txt")
        filt_lines = []
        f = open(u12_arg, "r")
        for num, line in enumerate(f, 1):
            line_data = line.rstrip().split("\t")
            if len(line_data) != U12_FILE_COLS:
                err_msg = (
                    f"Error! U12 file {u12_arg} line {num} is corrupted, 3 fields expected; "
                    f"Got {len(line_data)}; please note that a tab-separated file expected"
                )
                raise ValueError(err_msg)
            trans_id = line_data[0]
            if trans_id not in t_in_bed:
                # transcript doesn't appear in the bed file: skip it
                continue
            exon_num = line_data[1]
            if not exon_num.isnumeric():
                err_msg = (
                    f"Error! U12 file {u12_arg} line {num} is corrupted, "
                    f"field 2 value is {exon_num}; This field must "
                    f"contain a numeric value (exon number)."
                )
                raise ValueError(err_msg)
            acc_don = line_data[2]
            if acc_don not in U12_AD_FIELD:
                err_msg = (
                    f"Error! U12 file {u12_arg} line {num} is corrupted, field 3 value is {acc_don}"
                    f"; This field could have either A or D value."
                )
                raise ValueError(err_msg)
            filt_lines.append(line)  # save this line
        f.close()
        # another check: what if there are no lines after filter?
        if len(filt_lines) == 0:
            err_msg = (
                f"Error! No lines left in the {u12_arg} file after filter."
                f"Please check that transcript IDs in this file and input bed file are consistent"
            )
            raise ValueError(err_msg)
        with open(u12_saved_file, "w") as f:
            f.write("".join(filt_lines))
        return u12_saved_file

    @staticmethod
    def check_isoforms_file(isoforms_arg, t_in_bed, temp_wd):
        """Sanity checks for isoforms file."""
        if not isoforms_arg:
            to_log("Continue without isoforms file: not provided")
            return None  # not provided: nothing to check
        # isoforms file provided: need to check correctness and completeness
        # then check isoforms file itself
        _, isoform_to_gene, header = read_isoforms_file(isoforms_arg)
        header_maybe_gene = header[0]  # header is optional, if not the case: first field is a gene
        header_maybe_trans = header[1]  # and the second is the isoform
        # save filtered isoforms file here:  (without unused transcripts)
        isoforms_file = os.path.join(temp_wd, "isoforms.tsv")
        # this set contains isoforms found in the isoforms file
        t_in_i = set(isoform_to_gene.keys())
        # there are transcripts that appear in bed but not in the isoforms file
        # if this set is non-empty: raise an error
        u_in_b = t_in_bed.difference(t_in_i)

        if len(u_in_b) != 0:  # isoforms file is incomplete
            extra_t_list = "\n".join(
                list(u_in_b)[:100]
            )  # show first 100 (or maybe show all?)
            err_msg = (
                f"Error! There are {len(u_in_b)} transcripts in the bed "
                f"file absent in the isoforms file! "
                f"There are the transcripts (first 100):\n{extra_t_list}"
            )
            raise ValueError(err_msg)

        t_in_both = t_in_bed.intersection(t_in_i)  # isoforms data that we save
        # if header absent: second field found in the bed file
        # then we don't need to write the original header
        # if present -> let keep it
        # there is not absolutely correct: MAYBE there is no header at all, but
        # the first line of the isoforms file is not in the bed file, so we still will write it
        skip_header = header_maybe_trans in t_in_bed

        # write isoforms file
        f = open(isoforms_file, "w")
        if not skip_header:
            f.write(f"{header_maybe_gene}\t{header_maybe_trans}\n")
        to_log(f"Writing isoforms data for {len(t_in_both)} transcripts.")
        for trans in t_in_both:
            gene = isoform_to_gene[trans]
            f.write(f"{gene}\t{trans}\n")

        f.close()
        return isoforms_file

    @staticmethod
    def check_chains_classified(chain_results_df):
        """Check whether chain classification result is non-empty."""
        def has_more_than_one_line(file_path):
            with open(file_path, 'r') as f:
                return sum(1 for _ in islice(f, 2)) > 1

        is_complete = has_more_than_one_line(chain_results_df)
        if is_complete is False:
            msg = f"Chain results file {chain_results_df} is empty! Abort."
            to_log(msg)
            raise ValueError(msg)

    @staticmethod
    def check_dependencies(toga_cls):
        """Check all dependencies."""
        # TODO: refactor this part - different checks depending on the selected strategy
        not_nf = shutil.which(Constants.NEXTFLOW) is None
        if toga_cls.para_strategy == "nextflow" and not_nf:
            msg = (
                "Error! Cannot fild nextflow executable. Please make sure you "
                "have a nextflow binary in a directory listed in your $PATH"
            )
            toga_cls.die(msg)

        c_not_compiled = any(
            os.path.isfile(f) is False
            for f in [
                toga_cls.CHAIN_SCORE_FILTER,
                toga_cls.CHAIN_COORDS_CONVERT_LIB,
                toga_cls.CHAIN_FILTER_BY_ID,
                toga_cls.EXTRACT_SUBCHAIN_LIB,
                toga_cls.CHAIN_INDEX_SLIB,
            ]
        )
        if c_not_compiled:
            to_log("Warning! C code is not compiled, trying to compile...")

        imports_not_found = False
        required_libraries = [
            'twobitreader',
            'networkx',
            'pandas',
            'numpy',
            'xgboost',
            'scikit-learn',
            'joblib',
            'h5py'
        ]

        to_log("# Python package versions")
        for lib in required_libraries:
            try:
                lib_module = __import__(lib)
                if hasattr(lib_module, '__version__'):
                    lib_version = lib_module.__version__
                else:
                    lib_version = 'unknown version'
                to_log(f"* {lib}: {lib_version}")
            except ImportError:
                imports_not_found = True
                to_log(f"! {lib}: Not installed - will try to install")

        not_all_found = any([c_not_compiled, imports_not_found])
        call_process(
            toga_cls.CONFIGURE_SCRIPT, "Could not call configure.sh!"
        ) if not_all_found else None

    @staticmethod
    def check_completeness(toga_cls):
        """Check if all modules are presented."""
        files_must_be = [
            toga_cls.CONFIGURE_SCRIPT,
            toga_cls.CHAIN_BDB_INDEX,
            toga_cls.BED_BDB_INDEX,
            toga_cls.SPLIT_CHAIN_JOBS,
            toga_cls.MERGE_CHAINS_OUTPUT,
            toga_cls.CLASSIFY_CHAINS,
            toga_cls.SPLIT_EXON_REALIGN_JOBS,
            toga_cls.MERGE_CESAR_OUTPUT,
            toga_cls.GENE_LOSS_SUMMARY,
            toga_cls.ORTHOLOGY_TYPE_MAP,
        ]
        for _file in files_must_be:
            if os.path.isfile(_file):
                continue
            toga_cls.die(f"Error! File {_file} not found!")
