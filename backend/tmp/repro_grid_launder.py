"""Live repro: do registry version/display_name prose digits launder count claims,
and do z constants ground ONLY via that prose (tuple z_coverage never harvested)?"""
import copy
import json

from app.services import cosmology_likelihoods as cl
from app.services.claim_validator import validate_claims, _iter_numeric_values, _result_only_nodes

# 1. REAL chain result, exactly the executable path the chat tool uses.
r = cl.run_likelihood_chain(
    model="lcdm",
    dataset_keys=["eboss_dr16_elg_bao", "eboss_dr16_lyauto_bao", "eboss_dr16_lyxqso_bao"],
    n_samples=1500,
    random_seed=42,
)
print("chain_tier:", r["chain_tier"])
print("datasets_used keys:", [d["key"] for d in r["datasets_used"]])
print("z_coverage type in raw result:", type(r["datasets_used"][0]["z_coverage"]))

# 2. Wrap as chat.py wraps it (raw result object, accumulator envelope).
acc = [{"id": "t1", "tool": "run_cosmology_likelihood_chain", "input": {"model": "lcdm"}, "result": r}]

def universe(tool_results):
    vals = set()
    for node in _result_only_nodes(tool_results):
        vals |= set(_iter_numeric_values(node))
    return vals

u = universe(acc)
for probe in (399.0, 50.0, 0.845, 2.334, 18.33, 37.76):
    print(f"  {probe} in universe: {probe in u}")

# 3. Fabricated count claims against the chain result.
for claim in ("The fit used 399 quasars.", "We detected 50 quasars.",
              "The sample contains 2500 galaxies."):
    v = validate_claims(claim, acc)
    print(f"CLAIM {claim!r}: ok={v.ok} uncited={[(c.label, c.value) for c in v.uncited]} claims={[(c.label, c.value) for c in v.claims]}")

# 4. Honest z claims.
for claim in ("The Lyman-alpha BAO measurement is at z = 2.334.",
              "The ELG BAO point sits at z = 0.845."):
    v = validate_claims(claim, acc)
    print(f"CLAIM {claim!r}: ok={v.ok} uncited={[(c.label, c.value) for c in v.uncited]} claims={[(c.label, c.value) for c in v.claims]}")

# 5. Attribution: blank version + display_name everywhere, recompute universe.
r2 = copy.deepcopy(r)
def blank(node):
    if isinstance(node, dict):
        for k in ("version", "display_name", "dataset_version", "dataset_display_name"):
            if isinstance(node.get(k), str):
                node[k] = ""
        for v in node.values():
            blank(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            blank(v)
blank(r2)
acc2 = [{"id": "t1", "tool": "run_cosmology_likelihood_chain", "input": {}, "result": r2}]
u2 = universe(acc2)
print("after blanking version/display_name:")
for probe in (399.0, 50.0, 0.845, 2.334):
    print(f"  {probe} in universe: {probe in u2}")
for claim in ("The Lyman-alpha BAO measurement is at z = 2.334.",
              "The ELG BAO point sits at z = 0.845."):
    v = validate_claims(claim, acc2)
    print(f"BLANKED CLAIM {claim!r}: ok={v.ok}")

# 6. Tuple vs list: JSON round-trip turns tuples into lists — would z then enter?
r3 = json.loads(json.dumps(r2, default=str))
acc3 = [{"id": "t1", "tool": "x", "input": {}, "result": r3}]
u3 = universe(acc3)
print("after blanking AND json round-trip (tuple->list):")
for probe in (0.845, 2.334):
    print(f"  {probe} in universe: {probe in u3}")
