#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SYNOPSIS

    synth [-h,--help] [-v,--verbose] [--version]

DESCRIPTION

    TODO This describes how to use this script. This docstring
    will be printed by the script if there is an error or
    if the user requests help (-h or --help).

EXAMPLES

    TODO: Show some examples of how to use this script.

EXIT STATUS

    TODO: List exit codes

AUTHOR

    Sébastien Le Maguer <sebastien.le_maguer@irisa.fr>
    Marc Evrard <marc.evrard@limsi.fr>

LICENSE

    This script is in the public domain, free from copyrights or restrictions.

VERSION

    $Id$
"""

import sys
import os
import traceback
import argparse

import time
import subprocess  # Shell command calling
import re
import logging


################################################################################
### Constants
################################################################################
# FIXME: ugly as it points in a directory specified in the synth_from_roots.sh
# Configs
TRAIN_CONFIG = "tmp/train%d.cfg" % os.getpid()          # FIXME
SYNTH_CONFIG = "tmp/synth%d.cfg" % os.getpid()          # FIXME

# Lists
GV_TIED_LIST_TMP = "tmp/tiedlist_%d_gv" % os.getpid()   # FIXME
TYPE_TIED_LIST_BASE = "tmp/tiedlist_%d" % os.getpid()   # FIXME
LABEL_LIST_FN = "tmp/list_all%d" % os.getpid()          # FIXME
TMP_LABELS_LIST_FN = "tmp/list_input_labels%d" % os.getpid()

# Tmp Scripts
GV_HED_UNSEEN_BASE = "tmp/mku_%d_gv" % os.getpid()      # FIXME
TYPE_HED_UNSEEN_BASE = "tmp/mku_%d" % os.getpid()       # FIXME
STRAIGHT_SCRIPT = "tmp/straight_%d.m" % os.getpid()     # FIXME

# Models
TMP_GV_MMF = "tmp/gv_%d.mmf" % os.getpid()              # FIXME
TMP_CMP_MMF = "tmp/cmp_%d.mmf" % os.getpid()            # FIXME
TMP_DUR_MMF = "tmp/dur_%d.mmf" % os.getpid()            # FIXME

# Wanted types
TYPE_MAP = {"cmp": ("mgc", "lf0", "bap"), "dur": "dur"}

# Signal
PF_MCP = 1.4                                            # postfiltering factor for MGC
PF_LSP = 0.7                                            # postfiltering factor for LSP
FL = 576                                                # length of impulse response
CO = 2047                                               #
ORDER = {"mgc": 50, "lf0": 1, "bap": 25}
SAMPLERATE = 44100                                      # Signal samplerate
FRAMESHIFT = 220.5
FREQWARPING = 0.53                                      # [FIXME: DEPENDS ON SAMPLERATE]
GAMMA = 0                                               # [FIXME: see config]

# Generation
MINDUR = 5
MAXDEV_HSMM = 10
MAXEMITER = 20                    # max EM iteration
EMEPSILON = 0.0001                # convergence factor for EM iteration
MAXGVITER = 50                    # max GV iteration
GVEPSILON = 0.0001                # convergence factor for GV iteration
MINEUCNORM = 0.01                 # minimum Euclid norm for GV iteration
STEPINIT = 1.0                    # initial step size
STEPINC = 1.2                     # step size acceleration factor
STEPDEC = 0.5                     # step size deceleration factor
HMMWEIGHT = 1.0                   # weight for HMM output prob.
GVWEIGHT = 1.0                    # weight for GV output prob.
OPTKIND = 'NEWTON'                # optimization method (STEEPEST, NEWTON, or LBFGS)
CDGV = True                       # context-dependent GV

# GV
SLNT = ('pau', 'h#', 'brth', 'start', 'end', 'spause', 'insp')

# Tools
STRAIGHT_PATH = "STRAIGHTV40pcode"
WIN_PATH = "win"    # FIXME

# Other stuffs
BEAM_STEPS = "1500 100 5000"      # beam width control
VFLR = {"mgc": 0.01, "lf0": 0.01, "bap": 0.01, "dur": 0.01}
STRB = {"mgc": 1, "lf0": 2, "bap": 5, "dur": 1}
STRE = {"mgc": 1, "lf0": 4, "bap": 5, "dur": 1}
NWIN = {"mgc": 3, "lf0": 3, "bap": 3, "dur": 0}


################################################################################
### Config + script functions
################################################################################

def generate_label_list(input_label_list):
    """
    Generate the label list file to get it throught the tree
    """

    p = re.compile('[ \t]+')
    full_set = set()

    # Fullcontext list (Training + generation)
    for input_label in input_label_list:
        with open(input_label) as cur_lab_file:
            for cur_line in cur_lab_file:
                cur_line = cur_line.strip()
                lab = p.split(cur_line)[2]
                full_set.add(lab)

    with open(LABEL_LIST_FN, 'w') as f_list:
        f_list.write("\n".join(full_set))


def generate_training_configuration():
    """
    Generate "training configuration" => needed for the tree search
    """
    # Training configuration
    with open(TRAIN_CONFIG, "w") as f:
        f.write("NATURALREADORDER = T\n")
        f.write("NATURALWRITEORDER = T\n")

        # Variance floor options
        f.write("APPLYVFLOOR = T\n")
        f.write("VFLOORSCALESTR = \"Vector %d " % (max([STRE[cur_type]for cur_type in TYPE_MAP["cmp"]])))
        for cur_type in TYPE_MAP["cmp"]:
            f.write(" ".join(["%f" % VFLR[cur_type]] * (STRE[cur_type] - STRB[cur_type] + 1)))

        f.write("\"\n")
        f.write("APPLYDURVARFLOOR = T\n")
        f.write("DURVARFLOORPERCENTILE = %f\n" % (100 * VFLR["dur"]))

        # Duration specific
        f.write("MAXSTDDEVCOEF = %d\n" % MAXDEV_HSMM)
        f.write("MINDUR = %d\n" % MINDUR)


def generate_synthesis_configuration(use_gv):
    """
    Generate the synthesis configuration file needed by HMGenS
    """
    # Synthesis configuration

   # config file for parameter generation
    with open(SYNTH_CONFIG, "w") as f:

        # Global parameters
        f.write("NATURALREADORDER = T\n")
        f.write("NATURALWRITEORDER = T\n")
        f.write("USEALIGN = T\n")
        f.write("MAXEMITER = %d\n" % MAXEMITER)

        # Counting streams
        f.write("PDFSTRSIZE = \"IntVec %d" % len(TYPE_MAP["cmp"]))    # PdfStream structure
        for cur_type in TYPE_MAP["cmp"]:
            f.write(" %d" % (STRE[cur_type] - STRB[cur_type] + 1))
        f.write("\"\n")

        # Order of each coefficients
        f.write("PDFSTRORDER = \"IntVec %d" % len(TYPE_MAP["cmp"]))    # PdfStream structure
        for cur_type in TYPE_MAP["cmp"]:
            f.write(" %d" % (ORDER[cur_type]))
        f.write("\"\n")

        # Extension
        f.write("PDFSTREXT = \"StrVec %d %s\"\n" % (len(TYPE_MAP["cmp"]), " ".join(TYPE_MAP["cmp"])))

        # Windows
        f.write("WINFN = \"")
        for cur_type in TYPE_MAP["cmp"]:
            # FIXME in the middle of the source => move
            win_fn = " ".join("%s.win%d" % (cur_type, d) for d in range(1, NWIN[cur_type] + 1))
            f.write(" StrVec %d %s" % (NWIN[cur_type], win_fn))
        f.write("\"\n")
        f.write("WINDIR = %s\n" % WIN_PATH)

        # Global variance
        if use_gv:
            f.write("EMEPSILON  = %f\n" % EMEPSILON)
            f.write("USEGV      = TRUE\n")
            f.write("GVMODELMMF = %s\n" % TMP_GV_MMF)
            f.write("GVHMMLIST  = %s\n" % GV_TIED_LIST_TMP)
            f.write("MAXGVITER  = %d\n" % MAXGVITER)
            f.write("GVEPSILON  = %f\n" % GVEPSILON)
            f.write("MINEUCNORM = %f\n" % MINEUCNORM)
            f.write("STEPINIT   = %f\n" % STEPINIT)
            f.write("STEPINC    = %f\n" % STEPINC)
            f.write("STEPDEC    = %f\n" % STEPDEC)
            f.write("HMMWEIGHT  = %f\n" % HMMWEIGHT)
            f.write("GVWEIGHT   = %f\n" % GVWEIGHT)
            f.write("OPTKIND    = %s\n" % OPTKIND)

            f.write("GVOFFMODEL = \"StrVec %d %s\"\n" % (len(SLNT), " ".join(SLNT)))
            if CDGV:
                f.write("CDGV = TRUE\n")
            else:
                f.write("CDGV = FALSE\n")
        else:
            f.write("USEGV      = FALSE\n")


def mk_unseen_script(cmp_tree_dir, dur_tree_dir, use_gv, gv_dir=None):
    """
    Generate hed
    """

    # Generate GV script
    if use_gv:
        with open(GV_HED_UNSEEN_BASE + ".hed", "w") as f:
            f.write("\nTR 2\n\n")

            # Load trees
            f.write("// Load trees\n")
            for cur_type in TYPE_MAP["cmp"]:
                f.write("LT \"%s/%s.inf\"\n\n" % (gv_dir, cur_type))

            # Make unseen
            f.write("// Make unseen\n")
            f.write("AU \"%s\"\n\n" % LABEL_LIST_FN)

            # Compact model
            f.write("// Compact\n")
            f.write("CO \"%s\"\n\n" % GV_TIED_LIST_TMP)

    # CMP
    with open("%s_cmp.hed" % TYPE_HED_UNSEEN_BASE, "w") as f:
        f.write("\nTR 2\n\n")

        # Load trees
        f.write("// Load trees\n")
        for cur_type in TYPE_MAP["cmp"]:
            f.write("LT \"%s/%s.inf\"\n\n" % (cmp_tree_dir, cur_type))

        # Make unseen
        f.write("// Make unseen\n")
        f.write("AU \"%s\"\n\n" % LABEL_LIST_FN)

        # Compact model
        f.write("// Compact\n")
        f.write("CO \"%s_cmp\"\n\n" % TYPE_TIED_LIST_BASE)

    # CMP
    with open("%s_dur.hed" % TYPE_HED_UNSEEN_BASE, "w") as f:
        f.write("\nTR 2\n\n")

        # Load trees
        f.write("// Load trees\n")
        f.write("LT \"%s/dur.inf\"\n\n" % dur_tree_dir)

        # Make unseen
        f.write("// Make unseen\n")
        f.write("AU \"%s\"\n\n" % LABEL_LIST_FN)

        # Compact model
        f.write("// Compact\n")
        f.write("CO \"%s_dur\"\n\n" % TYPE_TIED_LIST_BASE)


################################################################################
### Parameter transformation function
################################################################################
# TODO : add post-filtering functions
# def postFilteringMCP(base, outdir):
#     """
#     """
#     strPF_MCP = "%f" % PF_MCP
#     cmd = "echo 1 1 %s | x2x +af > %s/weights" % (' '.join([strPF_MCP] * MGC_ORDER), outdir)

#     # FIXME: finish but not needed for the moment
#     pass

#     # # Clean
#     # os.remove("%s/weights" % outdir)


def parameter_conversion(outdir, base_list):
    """
    Convert parameter to STRAIGT params
    """

    for base in base_list:
        # lf0 => f0
        cmd = "sopr -magic -1.0E+10 -EXP -MAGIC 0.0 %s/%s.lf0" % \
            (outdir, base)
        with open("%s/%s.f0" % (outdir, base), 'w') as f:
            subprocess.call(cmd.split(" "), stdout=f)

        # bap => aperiodicity
        cmd = "mgc2sp -a %f -g 0 -m %d -l 2048 -o 0 %s/%s.bap" % \
            (FREQWARPING, ORDER["bap"]-1, outdir, base)
        with open("%s/%s.ap" % (outdir, base), 'w') as f:
            subprocess.call(cmd.split(" "), stdout=f)

        # mgc => spectrum
        cmd = "mgc2sp -a %f -g %f -m %d -l 2048 -o 2 %s/%s.mgc" % \
            (FREQWARPING, GAMMA, ORDER["mgc"]-1, outdir, base)
        with open("%s/%s.sp" % (outdir, base), 'w') as f:
            subprocess.call(cmd.split(" "), stdout=f)

        # Clean [FIXME: do with options]
        os.remove("%s/%s.lf0" % (outdir, base))
        os.remove("%s/%s.mgc" % (outdir, base))
        os.remove("%s/%s.bap" % (outdir, base))
        os.remove("%s/%s.dur" % (outdir, base))     # FIXME : must be an option in the synth config


def straight_generation(outdir, base_list):
    """
    """
    global out_handle
    
    # Generate STRAIGHT script
    with open(STRAIGHT_SCRIPT, "w") as f:
        # Header
        f.write("path(path, '%s');\n" % STRAIGHT_PATH)
        f.write("prm.spectralUpdateInterval = %f;\n" % (1000.0 * FRAMESHIFT / SAMPLERATE))
        f.write("prm.levelNormalizationIndicator = 0;\n\n")

        # Read STRAIGHT params
        for base in base_list:
            f.write("fid_sp = fopen('%s/%s.sp', 'r', 'ieee-le');\n" % (outdir, base))
            f.write("fid_ap = fopen('%s/%s.ap', 'r', 'ieee-le');\n" % (outdir, base))
            f.write("fid_f0 = fopen('%s/%s.f0', 'r', 'ieee-le');\n" % (outdir, base))

            nb_frames = os.path.getsize("%s/%s.f0" % (outdir, base)) / 4
            f.write("sp = fread(fid_sp, [%d %d], 'float');\n" % (1025, nb_frames))
            f.write("ap = fread(fid_ap, [%d %d], 'float');\n" % (1025, nb_frames))
            f.write("f0 = fread(fid_f0, [%d %d], 'float');\n" % (1, nb_frames))

            f.write("fclose(fid_sp);\n")
            f.write("fclose(fid_ap);\n")
            f.write("fclose(fid_f0);\n")

            # Spectrum normalisation # FIXME (why ?) => not compatible with our corpus podalydes
            # f.write("sp = sp * %f;\n" % (1024.0 / (2200.0 * 32768.0)))

            # Synthesis process part 2
            f.write("[sy] = exstraightsynth(f0, sp, ap, %d, prm);\n" % SAMPLERATE)
            f.write("wavwrite(sy, %d, '%s/%s.wav');\n" % (SAMPLERATE, outdir, base))

        # Ending
        f.write("quit;\n")

    # Synthesis !
    cmd = "matlab -nojvm -nosplash -nodisplay < %s" % STRAIGHT_SCRIPT
    subprocess.call(cmd.split(" "), stdout=out_handle)

    # Clean  [FIXME: do with options]
    os.remove(STRAIGHT_SCRIPT)
    for base in base_list:
        os.remove('%s/%s.sp' % (outdir, base))
        os.remove('%s/%s.ap' % (outdir, base))
        os.remove('%s/%s.f0' % (outdir, base))


################################################################################
### Main function
################################################################################

def setup_logging(is_verbose):
    """
    Setup logging according to the verbose mode
    """
    logging.basicConfig(format='[%(asctime)s] %(levelname)s : %(message)s')
    
    if not is_verbose:
        level = logging.INFO
    else:
        level = logging.DEBUG

    # handler = ColorizingStreamHandler(sys.stderr)
    # root.setLevel(logging.DEBUG)
    # if root.handlers:
    #     for handler in root.handlers:
    #         root.removeHandler(handler)
    # formatter = logging.Formatter(datefmt='%Y/%m/%d %H:%M',
    #                               fmt='[%(asctime)s %(name)s  %(levelname)s] %(message)s')
    # handler.setFormatter(formatter)
    # logging.getLogger().addHandler(handler)

    _logger = logging.getLogger("EXTRACT_STRAIGHT")
    _logger.setLevel(level)

    return _logger


def main():
    """Main entry function
    """
    global options, args, logger, out_handle

    use_gv = options.gvDir
    outdir = os.path.abspath(options.output)

    # 0. Generate list file
    list_input_files = TMP_LABELS_LIST_FN
    if options.synthList:
        list_input_files = options.input
    else:
        with open(list_input_files, "w") as f:
            f.write(options.input + "\n")

    base_list = []
    label_fn_list = []
    with open(list_input_files) as f:
        for line in f:
            label_fn_list.append(line.strip())
            base_list.append(os.path.splitext(os.path.basename(line.strip()))[0])
    
    generate_label_list(label_fn_list)

    # 1. Generate configs
    generate_training_configuration()
    generate_synthesis_configuration(use_gv)

    # 2. Generate scripts
    mk_unseen_script(options.cmpTreeDir, options.durTreeDir, use_gv, options.gvDir)

    # 3. Compose models
    #    - CMP
    logger.info("CMP unseen model building")
    cmd = "HHEd -A -B -C %s -D -T 1 -p -i -H %s -w %s %s %s" % \
        (TRAIN_CONFIG, options.cmpModelFn, TMP_CMP_MMF, TYPE_HED_UNSEEN_BASE + "_cmp.hed", options.inputList)
    subprocess.call(cmd.split(" "), stdout=out_handle)
    #    - DUR
    logger.info("Duration unseen model building")
    cmd = "HHEd -A -B -C %s -D -T 1 -p -i -H %s -w %s %s %s" % \
        (TRAIN_CONFIG, options.durModelFn, TMP_DUR_MMF,
            TYPE_HED_UNSEEN_BASE + "_dur.hed", options.inputList)
    subprocess.call(cmd.split(" "), stdout=out_handle)
    
    #    - GV
    if use_gv:
        logger.info("Global variance unseen model bulding")
        cmd = "HHEd -A -B -C %s -D -T 1 -p -i -H %s -w %s %s %s" % \
            (TRAIN_CONFIG, options.gvDir + "/clustered.mmf", TMP_GV_MMF,
                GV_HED_UNSEEN_BASE + ".hed", options.gvDir + "/gv.list")
        subprocess.call(cmd.split(" "), stdout=out_handle)

    # 4. Generate parameters
    logger.info("Parameter generation")
    cmd = "HMGenS -A -B -C %s -D -T 1 -S %s -t %s -c %d -H %s -N %s -M %s %s %s" % \
        (SYNTH_CONFIG, list_input_files, BEAM_STEPS, int(options.pgType),
            TMP_CMP_MMF, TMP_DUR_MMF, outdir,
            TYPE_TIED_LIST_BASE + "_cmp", TYPE_TIED_LIST_BASE + "_dur")
    subprocess.call(cmd.split(" "), stdout=out_handle)

    # 5. Call straight to synthesize
    logger.info("Parameter conversion (could be quite long)")
    parameter_conversion(outdir, base_list)
    
    logger.info("Audio rendering (could be quite long)")
    straight_generation(outdir, base_list)


################################################################################
### Enveloping
################################################################################

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(   # formatter=argparse.TitledHelpFormatter(),
                                            usage=globals()['__doc__'],
                                            version='$Id$'
        )
        parser.add_argument('-v', '--verbose', action='store_true',
                            default=False, help='verbose output')

        # models
        parser.add_argument("-m", "--cmp", dest="cmpModelFn",
                            help="cmp model file", metavar="FILE")
        parser.add_argument("-d", "--dur", dest="durModelFn",
                            help="duration model file", metavar="FILE")
        parser.add_argument("-l", "--list", dest="inputList",
                            help="input list file", metavar="FILE")
        parser.add_argument("-t", "--cmp_tree", dest="cmpTreeDir",
                            help="directory which contains the coefficient trees")
        parser.add_argument("-u", "--dur_tree", dest="durTreeDir",
                            help="directory which contains the duration tree")

        # Options
        parser.add_argument("-s", "--with_scp", dest="synthList", action='store_true',
                            default=False, help="the input is a scp formatted file")
        parser.add_argument("-g", "--gv", dest="gvDir",
                            help="Define the global variance model directory")

        parser.add_argument("-p", "--pg_type", dest="pgType",
                            help="parameter generation type")
        # input/output
        parser.add_argument("-i", "--input", dest="input",
                            help="input label file", metavar="FILE")
        parser.add_argument("-o", "--output", dest="output",
                            help="output wav directory", metavar="FILE")
        (options, args) = parser.parse_args()

         # Debug time
        logger = setup_logging(options.verbose)
        out_handle = open(os.devnull, "w")
        if options.verbose:
            out_handle.close()
            out_handle = sys.stdout
        
         # Debug time
        start_time = time.time()
        if options.verbose:
            logger.debug(time.asctime())

        # Running main function <=> run application
        main()

        # Debug time
        if options.verbose:
            logger.debug(time.asctime())
        if options.verbose:
            logger.debug('TOTAL TIME IN MINUTES: %f' % ((time.time() - start_time) / 60.0))

        # Exit program
        sys.exit(0)
    except KeyboardInterrupt as e:  # Ctrl-C
        raise e
    except SystemExit as e:         # sys.exit()
        pass
    except Exception as e:
        print('ERROR, UNEXPECTED EXCEPTION')
        print(str(e))
        traceback.print_exc()
        # os._exit(1)
        sys.exit(1)