/**
 * Single source of truth for the citation strings used across mock data and
 * UI. Kept in one map so the same regulation is never typo'd two different
 * ways in two different fixtures — a judge freeze-framing a citation chip
 * should see the same string every time it's the same rule.
 *
 * These mirror packages/rules/rules/{deadlines,eligibility,fronts,audit,
 * denial}.py exactly, as merged to main 2026-08-25 (STATUTE wo3-5, #6), so
 * the mock front-end tells the same legal story the real rules engine does
 * now that services/api can wire in for real.
 */
export const CITE = {
  FAP_WINDOW: "26 CFR 1.501(r)-4(b)(1)(iv)",
  FAP_ELIGIBILITY: "26 CFR 1.501(r)-4(b)(2)",
  FAP_DENIAL_DOCS: "26 CFR 1.501(r)-4(b)(3)",
  ECA_MORATORIUM: "26 CFR 1.501(r)-6(c)(3)(i)",
  ECA_NOTICE: "26 CFR 1.501(r)-6(c)(4)",
  CA_NO_DEADLINE: "Cal. Health & Safety Code §127405(e)(3)",
  IL_UNINSURED_DISCOUNT: "210 ILCS 89/10 (Hospital Uninsured Patient Discount Act)",
  IL_LATEST_OF: "210 ILCS 89/25(a)",
  PPDR_DEADLINE: "45 CFR 149.620(c)",
  PPDR_DELTA: "45 CFR 149.620(b)",
  PPDR_SCOPE: "45 CFR 149.610(a)",
  VALIDATION: "12 CFR 1006.34(b)",
  FDCPA: "15 USC 1692g(a)",
  // Front-level "audit is/isn't applicable" citation — matches fronts.py's
  // FrontDecision for the audit front exactly.
  ITEMIZED_BILL: "42 USC 1395b-7(b); 45 CFR Part 180",
  // A for-profit hospital owes no 1.501(r) duty at all — 26 CFR
  // 1.501(r)-1(b)(20) limits the whole subchapter to a "hospital
  // organization" as there defined (packages/rules/rules/fronts.py:118).
  FOR_PROFIT_NO_DUTY: "26 CFR 1.501(r)-1(b)(20)",
  // Specific audit-finding citations (packages/rules/rules/audit.py) — more
  // precise than the front-level ITEMIZED_BILL citation above, used on the
  // individual events that report a finding.
  NCCI_EDIT:
    "CMS National Correct Coding Initiative; 42 CFR 447.45(b) (eff. Apr. 1, 2011), implementing ACA §6507 / 42 USC 1396b(r)(1)(B)",
  DUPLICATE_BILLING: "42 USC 1395b-7(b) (itemized statement); duplicate billing is not separately regulated",
  PRICE_TRANSPARENCY: "45 CFR 180.40, 180.50 (hospital price transparency; discounted cash price, eff. Jan. 1, 2021)",
} as const;
