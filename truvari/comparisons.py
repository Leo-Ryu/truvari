"""
Collection of methods with event helpers
that compare events, coordinates, or transform vcf entries
"""
# pylint: disable=no-member
import re
import logging
from functools import cmp_to_key
import Levenshtein


def entry_is_variant(entry, sample):
    """
    Returns if entry is non-ref variant
    """
    return "GT" in entry.samples[sample] and None not in entry.samples[sample]["GT"]


def vcf_to_key(source,  entry):
def vcf_to_key(source, entry):
    """
    Turn a vcf entry into a hashable key using the 'source' (base/comp) to separate the two
    setsource.chr:pos:ref:alt
    helpful for not re-using variants
    BUG: if a caller redundantly calls a variant exactly the same. It will be collapsed
    the
    """
    start, end = get_vcf_boundaries(entry)
    return "%s.%s:%d-%d(%s|%s)" % (source, entry.chrom, start, end, entry.ref, entry.alts[0])


def var_sizesim(sizeA, sizeB):
    """
    Calculate the size similarity pct for the two entries
    compares the longer of entryA's two alleles (REF or ALT)
    backwards compat
    """
    return min(sizeA, sizeB) / float(max(sizeA, sizeB)), sizeA - sizeB


def size_similarity(sizeA, sizeB):
    """
    Calculate the similarity pct for the two sizes
    """
    return var_sizesim(sizeA, sizeB)


def get_vcf_size_similarity(entryA, entryB):
    """
    Calculate the size similarity pct for the two entries
    compares the longer of entryA's two alleles (REF or ALT)
    """
    sizeA = get_vcf_entry_size(entryA)
    sizeB = get_vcf_entry_size(entryB)
    return var_sizesim(sizeA, sizeB)


def gt_comp(entryA, entryB, sampleA, sampleB):
    """
    Compare the genotypes, returns if they're the same
    Simple for now.
    """
    return entryA.samples[sampleA]["GT"] == entryB.samples[sampleB]["GT"]


def create_haplotype(entryA, entryB, ref):
    """
    Turn two entries into their haplotype sequence for comparison
    """
    def get_props(entry):
        """
        We compare the longer of the ref/alt sequence to increase comparability
        """
        if entry.alts[0] == "<DEL>" or len(entry.alts[0]) < len(entry.ref):
            return entry.chrom, entry.start, entry.stop, ref.get_seq(entry.chrom, entry.start, entry.stop).seq
        return entry.chrom, entry.start, entry.stop, entry.alts[0]

    a1_chrom, a1_start, a1_end, a1_seq = get_props(entryA)
    a2_chrom, a2_start, a2_end, a2_seq = get_props(entryB)

    start = min(a1_start, a2_start)
    end = max(a1_end, a2_end)

    hap1_seq = ref.get_seq(a1_chrom, start + 1, a1_start).seq + a1_seq + ref.get_seq(a1_chrom, a1_end + 1, end).seq
    hap2_seq = ref.get_seq(a2_chrom, start + 1, a2_start).seq + a2_seq + ref.get_seq(a2_chrom, a2_end + 1, end).seq
    return str(hap1_seq), str(hap2_seq)


def var_pctsim_lev(entryA, entryB, ref):
    """
    Use Levenshtein distance ratio of the larger sequence as a proxy
    to pct sequence similarity
    """
    # Shortcut to save compute - probably unneeded
    if entryA.ref == entryB.ref and entryA.alts[0] == entryB.alts[0]:
        return 1.0
    # Handling of breakends should be here
    try:
        allele1, allele2 = create_haplotype(entryA, entryB, ref)
    except Exception: #pylint: disable=broad-except
        return 0
    return Levenshtein.ratio(allele1, allele2)


def overlaps(s1, e1, s2, e2):
    """
    Check if two start/end ranges have overlap
    """
    s_cand = max(s1, s2)
    e_cand = min(e1, e2)
    return s_cand < e_cand


def get_vcf_variant_type(entry):
    """
    How svtype is determined:
    - Starts by trying to use INFO/SVTYPE
    - If SVTYPE is unavailable, infer if entry is a insertion or deletion by
      looking at the REF/ALT sequence size differences
    - If REF/ALT sequences are not available, try to parse the <INS>, <DEL>,
      etc from the ALT column.
    - Otherwise, assume 'UNK'
    """
    sv_alt_match = re.compile(r"\<(?P<SVTYPE>.*)\>")

    ret_type = None
    if "SVTYPE" in entry.info:
        ret_type = entry.info["SVTYPE"]
        if type(ret_type) is list:
            logging.warning("SVTYPE is list for entry %s", str(entry))
            ret_type = ret_type[0]
        return ret_type

    if not (entry.alts[0].count("<") or entry.alts[0].count(":")):
        # Doesn't have <INS> or BNDs as the alt seq, then we can assume it's sequence resolved..?
        if len(entry.ref) <= len(entry.alts[0]):
            ret_type = "INS"
        elif len(entry.ref) >= len(entry.alts[0]):
            ret_type = "DEL"
        elif len(entry.ref) == len(entry.alts[0]):
            # Is it really?
            ret_type = "COMPLEX"
        return ret_type
    mat = sv_alt_match.match(entry.alts[0])
    if mat is not None:
        return mat.groupdict()["SVTYPE"]
    logging.warning("SVTYPE is undetermined for entry, using 'UNK' - %s", str(entry))
    return "UNK"


def same_variant_type(entryA, entryB):
    """
    returns if entryA svtype == entryB svtype
    """
    a_type = get_vcf_variant_type(entryA)
    b_type = get_vcf_variant_type(entryB)
    return a_type == b_type


def fetch_coords(lookup, entry, dist=0):
    """
    Get the minimum/maximum fetch coordinates to find all variants within dist of variant
    """

    start, end = get_vcf_boundaries(entry)
    start -= dist
    end += dist
    # Membership queries are fastest O(1)
    if not lookup[entry.chrom].overlaps(start, end):
        return None, None

    cand_intervals = lookup[entry.chrom].overlap(start, end)
    s_ret = min([x.data for x in cand_intervals if overlaps(start, end, x[0], x[1])])
    e_ret = max([x.data for x in cand_intervals if overlaps(start, end, x[0], x[1])])
    return s_ret, e_ret


def get_vcf_boundaries(entry):
    """
    Get the start/end of an entry and order (start < end)
    """
    start = entry.start
    end = entry.stop
    return start, end


def get_vcf_entry_size(entry):
    """
    returns the size of the variant.

    How size is determined:
    - Starts by trying to use INFO/SVLEN
    - If SVLEN is unavailable and ALT field is an SV (e.g. <INS>, <DEL>, etc),
      use abs(vcf.start - vcf.end). The INFO/END tag needs to be available,
      especially for INS.
    - Otherwise, return the size difference of the sequence resolved call using
      abs(len(vcf.REF) - len(str(vcf.ALT[0])))
    """
    if "SVLEN" in entry.info:
        if type(entry.info["SVLEN"]) in [list, tuple]:
            size = abs(entry.info["SVLEN"][0])
        else:
            size = abs(entry.info["SVLEN"])
    elif entry.alts[0].count("<"):
        start, end = get_vcf_boundaries(entry)
        size = end - start
    else:
        size = abs(len(entry.ref) - len(entry.alts[0]))
    return size


def get_rec_ovl(astart, aend, bstart, bend):
    """
    Compute reciprocal overlap between two spans
    backwards compatibility
    """
    ovl_start = max(astart, bstart)
    ovl_end = min(aend, bend)
    if ovl_start < ovl_end:  # Otherwise, they're not overlapping
        ovl_pct = float(ovl_end - ovl_start) / max(aend - astart, bend - bstart)
    else:
        ovl_pct = 0
    return ovl_pct


def get_weighted_score(sim, size, ovl):
    """
    Unite the similarity measures and make a score
    return (2*sim + 1*size + 1*ovl) / 3.0
    """
    return (2 * sim + 1 * size + 1 * ovl) / 3.0


def reciprocal_overlap(astart, aend, bstart, bend):
    """
    creates a reciprocal overlap rule for matching two entries. Returns a method that can be used as a match operator
    """
    return get_rec_ovl(astart, aend, bstart, bend)


def get_vcf_reciprocal_overlap(entry1, entry2):
    """
    creates a reciprocal overlap rule for matching two entries. Returns a method that can be used as a match operator
    """
    astart, aend = get_vcf_boundaries(entry1)
    bstart, bend = get_vcf_boundaries(entry2)
    return reciprocal_overlap(astart, aend, bstart, bend)


def is_sv(entry, min_size=25):
    """
    Returns if the event is a variant over a minimum size
    """
    return get_vcf_entry_size(entry) >= min_size




def filter_value(entry, values=None):
    """
    Only take calls with filter values in the list provided
    if None provided, assume that filter_value must be PASS or blank '.')
    """
    if values is None:
        return entry.filter == [] or 'PASS' in entry.filter
    return values.intersection(entry.filter)


def match_sorter(candidates):
    """
    sort a list of tuples with similarity scores by priority, ignoring the entry_idx, but still sorting deterministically
    NOTE - last item in tuple must be the entry being priortized
    """
    if len(candidates) == 0:
        return
    entry_idx = len(candidates[0]) - 1

    def sort_cmp(mat1, mat2):
        """
        Sort by attributes and then deterministically by hash(str(VariantRecord))
        """
        for i in range(entry_idx):
            if mat1[i] != mat2[i]:
                return mat1[i] - mat2[i]
        return hash(str(mat1[entry_idx])) - hash(str(mat2[entry_idx]))

    candidates.sort(reverse=True, key=cmp_to_key(sort_cmp))


def copy_entry(entry, header):
    """
    For making entries editable
    """
    ret = header.new_record(contig=entry.chrom, start=entry.start, stop=entry.stop,
                            alleles=entry.alleles, id=entry.id, qual=entry.qual, filter=entry.filter,
                            info=entry.info)
    # should be able to just say samples=entry.samples...
    for sample in entry.samples:
        for k, v in entry.samples[sample].items():
            try:  # this will be a problem for pVCFs with differing Number=./A/G and set on input as (None,).. maybe
                ret.samples[sample][k] = v
            except TypeError:
                pass

    return ret
