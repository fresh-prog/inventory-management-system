# 07 — Legal & Compliance Research Notes (Uganda, Eritrea)

> **Not legal advice. Not a compliance claim.** These are engineering research notes from
> secondary sources, gathered so the system's design leaves room for the obligations below.
> Every item marked ⚠ must be confirmed by a qualified lawyer or tax advisor in each country
> before launch.

## Uganda

| Topic | What the research found | Design implication |
|---|---|---|
| Personal data | Data Protection and Privacy Act, 2019 (+ Regulations 2021), overseen by the Personal Data Protection Office (PDPO, under NITA-U). Controllers/processors register with the PDPO (online; fee reported around UGX 100,000; renewal reported as annual ⚠). The PDPO's 2025 decision against Google shows it enforces registration and transfer rules, including against offshore entities. | The restaurant (and we, if we host the cloud) may need to register. A privacy notice and data-protection policy are needed. Keep a register of the personal data processed (users, employees, supplier contacts). |
| Cross-border transfer | Section 19 restricts transfers outside Uganda unless the destination has adequate protection or the data subject consents ⚠. Sources disagree on scope. | The cloud region is a configuration choice. Branch-local storage is the default; cloud sync of personal data is opt-in and documented. Collect a transfer undertaking. Minimise personal data in sync payloads. |
| Employee data | Covered by the same Act; the Employment Act also applies ⚠. | Store only what the app needs (name, role, login). No national IDs in MVP. |
| Tax invoices / e-receipting | EFRIS (URA's Electronic Fiscal Receipting and Invoicing Solution) is mandatory for VAT-registered taxpayers since 1 Jan 2021 under the Tax Procedures Code Act s.73A–73B and the 2020 e-invoicing regulations. It was extended to further sectors from 1 Jul 2025 regardless of VAT registration ⚠ (check whether restaurants/hospitality are covered). Expenses without a valid EFRIS invoice from an EFRIS-obliged supplier may not be deductible. | Capture the supplier **TIN** and **EFRIS invoice/FDN number** on purchases (add fields in Stage 4). Flag purchases missing them. *Sales-side* EFRIS integration belongs to the future POS module. |
| Record retention | Taxpayers must keep their own statutory records; the exact retention period was not confirmed from primary sources ⚠ (commonly cited as 5+ years). | Retention is configurable, default **10 years**; confirmed financial records cannot be deleted; archives are exportable. |

## Eritrea

| Topic | What the research found | Design implication |
|---|---|---|
| Personal data | No comprehensive data protection law or authority was identified. One source cites an ICT law, but this could not be verified ⚠. The Penal Code covers some computer offences. | Apply the same baseline protections as Uganda (minimisation, access control, encryption, audit) as good practice. |
| Data storage / transfers | Internet connectivity is limited and expensive; no transfer law was identified ⚠. | Offline-first by default. A cloud copy is optional and owner-controlled. |
| Tax records & invoices | Not found in the sources searched; primary texts (proclamations, e.g. on income tax and sales tax) must be reviewed locally ⚠. | Configurable invoice fields and retention; multi-currency (ERN) support. |

## Common items (both countries)

| Topic | Approach |
|---|---|
| Payment handling | The app **records** supplier payments (cash, bank, mobile money); it does not move money. No card data is stored (out of PCI scope). |
| Data ownership | Customer agreement to state: the restaurant owns its business data; it may export everything (CSV/JSON) at any time; we process it as a processor. |
| Software licensing | Dependencies limited to permissive licences (MIT, BSD, Apache-2.0, PostgreSQL). Tesseract is Apache-2.0. A licence inventory (SBOM via `pip-licenses`/`license-checker`) is checked in CI from Stage 2. No GPL code in the distributed app without review. |
| Documents to prepare before commercial launch | Privacy policy · terms of service / customer agreement · data processing agreement · PDPO registration (Uganda) · internal data-protection policy · breach-response procedure. All need legal review. |

## Sources (secondary — verify against primary texts)
* [DLA Piper — Uganda regulator clarifies requirements for offshore entities (2025)](https://privacymatters.dlapiper.com/2025/08/uganda-data-protection-regulator-clarifies-compliance-requirements-for-offshore-entities)
* [Cliffe Dekker Hofmeyr — PDPO decision on registration of data controllers](https://www.cliffedekkerhofmeyr.com/en/news/publications/2025/Sectors/Technology-Communications/technology-and-communications-alert-13-august-to-register-or-not-to-register-the-ugandan-personal-data-protection-offices-decision-on-the-registration-of-data-controllers)
* [Global Law Experts — International data transfers in Uganda](https://globallawexperts.com/international-data-transfers-uganda/)
* [Uganda Data Protection and Privacy Act 2019 (ULII)](https://ulii.org/akn/ug/act/2019/9/eng@2019-05-03/source)
* [URA — EFRIS](https://ura.go.ug/en/efris/) · [URA — Additional taxpayers required to use EFRIS](https://ura.go.ug/?p=71626) · [EFRIS Handbook FY 2026/27](https://ura.go.ug/storage/2026/08/EFRIS-HANDBOOK-FY-2026-27.pdf)
* [PwC Uganda — EFRIS compliance](https://www.pwc.com/ug/en/press-room/efris-compliance.html)
* [dataprotection.africa — Eritrea](https://dataprotection.africa/eritrea/) · [Council of Europe Octopus — Eritrea](https://www.coe.int/web/octopus/-/eritrea)
