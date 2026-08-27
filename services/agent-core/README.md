# `services/agent-core/`

**Owner:** SWARM (persona 5)

ADK agent hierarchy + Pub/Sub push subscriber. Must be idempotent on redelivery (contract 2.3).

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[SWARM] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.

---

## HANDOFF — SWARM → FORGE: three §3.1 additions for patient-stated facts

**Context.** A real emailed bill (Sutter Bay / CA / self-pay, $2,625 billed,
$1,925 GFE, $32,000 pay stub) reached `select_fronts` with income and state
established and exactly one fact missing: **household size**. No §3.1 document
type states it and none can — a pay stub names an employee's earnings, never
who else lives in their home. Charity care correctly refused. The patient had
written the answer in the covering email:

> "I'm uninsured and paying out of pocket. Household of three, I make about
> $32,000 a year."

At household 3, $32,000 is 117% of the 2026 FPL and clears California's 400%
statutory floor (Cal. HSC §127405(a)(1)(A)) — free care, the entire bill
erased. Nothing read the email body. It now does, on a rail that is
deliberately separate from documents all the way down. Three fields need §3.1
to say so; **nothing here has been written into `BUILD_PLAYBOOK.md`.**

### 1. `documents[].type` gains `"patient_statement"`

```
cases/{case_id}/documents/{doc_id}
  type: "bill"|"itemized_bill"|"denial_letter"|"collection_notice"|"gfe"
      |"income_proof"|"patient_statement"|"generated_application"|"generated_letter"
```

The body of the email a bill arrived attached to. `services/intake` stores it
to GCS beside the attachments and publishes it on the existing
`case.document.added` topic (§3.2 unchanged) with `raw_text` set — the event
already carries that field.

*Why a document and not a case field:* the body needs exactly what every other
intake artifact needs — a stable id, a dedupe claim, a GCS object a human can
open, a place in `GET /cases/{id}`'s document list, and a Reader pass. A
case-level field would have re-implemented all five.

*The type is what makes it safe.* `agent_core.factmerge.INCOMING_DOC_TYPES`
deliberately EXCLUDES it, so nothing extracted from a patient statement can
reach `patient` or `bill` through the merge — by construction, not by every
precedence entry remembering to leave it out.

### 2. `cases/{case_id}.patient_stated`

```
patient_stated: {                       # what the patient CLAIMS, never a fact
  <patient field>: {value, quote, source: "patient_statement", source_doc_id}
}
```

A strictly third tier, below a human-entered value (§3.3 `POST /cases`) and
below a document. It is never merged into `patient`; it is applied as a derived
overlay for the duration of one `select_fronts` call and discarded. `patient`
keeps meaning exactly what it meant before — established. Only three fields are
admitted (`household_size`, `annual_income_cents`, `insured`); a patient's
recollection of a bill amount or a date is a worse copy of a document already
on the case.

### 3. `fronts[].provisional` and `fronts[].rests_on`

```
fronts: [{front, applicable, reason, deadline, status,
          provisional: bool,            # ADDED
          rests_on: [str]}]             # ADDED -- the patient-stated fields
                                        # this front's outcome actually turns on
```

Computed by leave-one-out over `select_fronts` (pure, no LLM), so the
attribution is exact rather than a rule of thumb. `reason` is prefixed by CODE
with the provenance and the patient's verbatim words — same rule as
`pipeline._SIMULATED_PREFIX`: whether a determination rests on a document or on
a sentence someone typed is a fact, not narration's to phrase or omit.

### What the live case now does, and why that is the honest answer

`charity_care` is **applicable, provisional, and blocked at filing**:

* the determination is real arithmetic over STATUTE's own thresholds and is
  shown in full, so an advocate learns the missing number is worth $2,625;
* `savings_found_cents` stays **$210** — the audit findings. The $2,625 is
  reported in the audit trail as a condition ("if the patient's own stated
  household size is confirmed, a free-care determination would erase $2,625.00
  — NOT counted"), never in the integer;
* §3.4 `charity_eligible` does **not** count it (see below);
* the Verifier refuses the filing, naming the fact and quoting the patient, and
  says exactly what clears it. Supply `patient.household_size` and the same
  determination becomes established, non-provisional, countable and fileable.

### Two smaller notes

* **§3.4 semantics, no key change.** `charity_eligible` / `ppdr_eligible` now
  count applicable **and not provisional** fronts. The keys are untouched; the
  meaning is "screened on evidence". Understating is the only safe direction
  for a banner a judge does arithmetic against — and the case detail carries
  the whole provisional determination, so nothing is hidden.
* **§3.3 gap.** There is no endpoint to supply one missing fact to an EXISTING
  case. `POST /cases` creates a new one. CANVAS's intake form (§4 persona 6
  WO4) needs something like `PATCH /cases/{id} {patient}` to close the loop the
  Verifier's refusal opens. Not built — that is a §3.3 change, and §0 rule 3
  says propose, don't diverge.

### Known limitation, not papered over

A follow-up reply carrying prose and **no attachment** is dropped. The body is
published only when the same message also carries a PDF, because
`history.list` is scoped to INBOX and INBOX still admits every newsletter the
demo account receives — publishing unconditionally would open a case per
email, live, on camera. Admitting a bare reply needs a signal that the thread
is already a case, and `ef-intake` has no Firestore grant to ask.
