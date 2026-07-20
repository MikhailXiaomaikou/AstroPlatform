# Scientific data licenses and acknowledgements

The Apache-2.0 license in the repository root covers Standard Astro source
code and project-authored documentation. It does **not** automatically cover
third-party catalogues, likelihood tables, chains, images, papers, or other
scientific data.

Every executable dataset or imported analysis product must record:

- the producing collaboration and release;
- the official source URL and citation;
- the upstream license or data-use policy;
- whether redistribution is allowed;
- the exact files vendored by this repository, if any;
- the required acknowledgement text; and
- a SHA-256 digest for every vendored or cached scientific file.

The dataset registry remains the source of truth for individual entries. If a
license or redistribution term is missing or unclear, the product may be
registered as a capability gap, but its bytes must not be redistributed and
it must not become executable evidence.

Repository users remain responsible for complying with each upstream data
provider's terms when downloading data at runtime.

## Union3 / UNITY1.5 22-node distance product

- Producer: Union3 collaboration; Rubin et al.,
  [arXiv `2311.12098v4`](https://arxiv.org/abs/2311.12098v4).
- Canonical release: [`rubind/union3_release` release `v1.0`](https://github.com/rubind/union3_release/tree/v1.0), commit
  `7f805c9cc4e7643f0392faad03a275094501f8a2`.
- Upstream license: [MIT](https://github.com/rubind/union3_release/blob/v1.0/LICENSE),
  as published in the canonical release repository.
- Exact text-format mirror used for the registered workflow:
  [`CobayaSampler/sn_data`](https://github.com/CobayaSampler/sn_data/tree/261e3564f532964b83647ae88b3d2eb01a015257/Union3), commit
  `261e3564f532964b83647ae88b3d2eb01a015257`, directory `Union3/`.
- Redistribution: permitted under the canonical Union3 release's MIT terms;
  retain this acknowledgement and the upstream copyright/license notice.
- Vendored files:
  - `backend/data/union3/lcparam_full.txt` — SHA-256
    `a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7`;
  - `backend/data/union3/mag_covmat.txt` — SHA-256
    `64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146`.
- Required acknowledgement: cite Rubin et al. (Union3) and identify the
  exact version, data hashes, and the intermediate Cobaya data mirror.

This registration covers only the two compressed 22-node files above. It
does not grant permission to redistribute the full paper, individual
supernova light curves, or unrelated contents of either repository.
