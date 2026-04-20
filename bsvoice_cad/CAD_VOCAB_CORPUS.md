---
title: "BSVoice-CAD — CAD Vocabulary Corpus Manifest"
subtitle: "Machine-readable catalog of authoritative engineering vocabularies for BSMS-CAD"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
version: "1.0.0"
status: "Scoping document — governs SPEC-07 content"
---

# BSVoice-CAD — CAD Vocabulary Corpus Manifest

## 0. Why This Document Exists

BSVoice-CAD's moat is not the Anthropic API call. The moat is what the agent does BEFORE the API call: **translate natural language into a canonical ID** that already exists in an authoritative engineering standard. When a user says "6-32 UNC tapped hole," the agent does not guess — it looks up `ASME_B1_1:6-32_UNC_2B` in a table and pulls the exact major diameter, pitch, tap drill, and tolerance class. The AI is the translator. The database is ground truth. The SolidWorks API is the actuator.

This document catalogs every vocabulary domain that BSVoice-CAD must eventually ingest. It is the scoping document that turns the abstract claim "BSMS applies to CAD" into concrete ETL work with named sources, row counts, and demo slices.

**This document is the input to SPEC-07 "CAD Vocabulary Layer"**, not SPEC-07 itself. SPEC-07 will specify the BSMS pipeline (bouncer persona, query planner, Sonnet synthesis, citation format). This document specifies what the pipeline queries over.

## 1. Architectural Frame

Every vocabulary domain in this corpus shares the same shape:

```
user_utterance → Bouncer (domain classifier, rejects out-of-scope) 
              → Intent + candidate domain(s)
              → SQL lookup against authoritative table(s)
              → Sonnet synthesis with row-level citation
              → Structured parameters for SolidWorks tool call
              → DHF entry with citation metadata
```

The **bouncer persona** for CAD is different from MIRA's "retired medical-device M&P engineer." Proposed persona: **"Retired aerospace toolmaker and QC inspector with 40 years across MS/AN/NAS fasteners, ASME Y14.5 GD&T, and multi-industry CAD workflows."** This persona has the authority to distinguish legitimate engineering vocabulary from freeform natural language, and to reject prompts like "make it look cool" or "do whatever the customer wants" as out-of-scope for a deterministic CAD interface.

Every vocabulary row MUST include:
- `canonical_id` — stable string identifier (e.g., `ASME_B1_1:6-32_UNC_2B`)
- `standard_source` — reference to the standard that defines this row (e.g., `ASME B1.1-2019`)
- `revision_year` — which revision of the standard this row reflects
- `aliases` — natural-language phrases that resolve to this row (e.g., `["6-32", "6-32 thread", "number-six thirty-two"]`)
- `superseded_by` — canonical_id of the row that replaces this one, if deprecated
- `domain` — which vocabulary table this belongs to

## 2. Domain Catalog

### 2.1 Prioritization key

**Tier 1 (MUST for hackathon demo):** Domains where a live voice demo fails without this lookup. Seed 50-300 rows each.
**Tier 2 (SHOULD for v1.0 Medtronic):** Domains that Medtronic will validate against. Full ingestion before any customer commitment.
**Tier 3 (v2+):** Domains required for aerospace/defense crossover. Deferred until after medtech foothold.

**Effort key:**
- **S (small):** ≤1 day ETL for full ingestion. Public enum, machine-readable.
- **M (medium):** 2–5 days ETL. Mostly machine-readable with some manual cleanup.
- **L (large):** 1–3 weeks ETL. PDF extraction, OCR, or manual curation required.
- **XL:** Months. Requires licensed data or deep per-row review.

---

### 2.2 SolidWorks API Domain (internal vocabulary)

These are not published engineering standards; they're the SolidWorks API surface itself. Without them, the agent cannot construct valid tool calls — it has to guess at enum names.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Feature types (`swFeatureNameID_e` + friends) | ~400 | SolidWorks API Help → Type Libraries (COM IDL extraction) | 1 | S |
| User preference values (`swUserPreference*`) | ~600 | SolidWorks API Help → Constants reference | 1 | S |
| Mate types (`swMateType_e` + advanced mate enums) | ~200 | SolidWorks API Help + advanced mate docs | 1 | S |
| Dimension types (`swDimensionType_e` + modifiers) | ~150 | SolidWorks API Help | 1 | S |
| Sketch entity types (`swSketchSegments_e` + others) | ~80 | SolidWorks API Help | 1 | S |
| Hole types (HoleWizard `swWzdGeneralHoleTypes_e` + standards enums) | ~250 | SolidWorks API Help → HoleWizard reference | 1 | S |
| Surface/face property names | ~120 | SolidWorks Custom Properties reference | 2 | S |
| View types (drawings) | ~40 | SolidWorks drawing API | 2 | S |
| Annotation types | ~180 | SolidWorks API annotation reference | 2 | S |
| **Subtotal: SolidWorks API** | **~2,020** | — | — | — |

**Extraction method:** SolidWorks ships type libraries (`.tlb` files) with every install. A C# tool can reflect over these at build time and emit JSON. This is pure code; zero manual work. **Half a day for the whole subtotal.**

**Demo slice (hackathon):** The 9-tool mandatory demo needs:
- Feature types: ~15 (Boss-Extrude, Cut-Extrude, Fillet, Chamfer, Hole, Mirror, Pattern, Sketch, Reference Plane, Axis, Point, Coordinate System, Origin, MateGroup, Revolve) — seed these
- Dimension types: ~10 (Linear, Angular, Radial, Diameter, Arc Length, Horizontal, Vertical, Ordinate, Chamfer, Path)
- Hole types: ~20 (the named ones from the HoleWizard tree for the demo)

---

### 2.3 Threading Standards

The biggest single win for demo credibility. "6-32 UNC" vs "M6×1.0" vs "1/4-20 UNC" — a CAD-aware AI that handles these correctly is night-and-day different from one that doesn't.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| UN / UNC / UNF / UNEF (Unified Inch) | ~200 | ASME B1.1-2019 Tables 1A–6B | 1 | S |
| UNS special (outside standard series) | ~400 | ASME B1.1 Appendix + ASME B1.15 | 2 | M |
| Metric ISO 261 (coarse + fine) | ~180 | ISO 261:1998 Table 1; ISO 262 | 1 | S |
| Metric ISO 724 (basic dimensions) | ~180 | ISO 724:1993 | 1 | S |
| BSP parallel / BSPT taper (G, Rp, R, Rc) | ~40 | ISO 228-1, ISO 7-1 | 1 | S |
| NPT / NPTF (American pipe, taper + dryseal) | ~30 | ASME B1.20.1, ASME B1.20.3 | 1 | S |
| ACME (general, centralizing, stub) | ~60 | ASME B1.5, ASME B1.8, ASME B1.9 | 2 | S |
| Trapezoidal (ISO 2901/2902/2903) | ~80 | ISO 2901:2023 | 2 | S |
| Aerospace MJ (UNJ internal + external) | ~200 | MIL-S-8879C, ANSI B1.15 | 3 | M |
| Buttress (ASME B1.9) | ~40 | ASME B1.9 | 3 | S |
| Whitworth BSW/BSF (legacy) | ~40 | BS 84:2007 | 3 | S |
| Model engineering (ME, BA) | ~80 | legacy catalogs, mostly for repair work | 3 | M |
| Thread tolerance classes | ~40 | embedded in each standard | 1 | S |
| **Subtotal: Threads** | **~1,570** | — | — | — |

**Demo slice:** 50 rows covering UNC, UNF, metric coarse (all common sizes — 2-56 through 1"-8; M2 through M24). Every tapped hole the demo script might hit.

**Why this matters:** a typical ChatGPT-based tool asked "add a 10-32 tapped hole" will produce a hole with wrong drill size, wrong depth convention, no tolerance class. A BSVoice-CAD agent queries `ASME_B1_1:10-32_UNF_2B` → gets `major_diameter=0.190", tap_drill=#21 (0.159"), tpi=32, tolerance_class=2B` → passes structured values to SolidWorks HoleWizard → correct hole, every time, with citation to ASME B1.1. **That's the demo moment.**

---

### 2.4 GD&T: ASME Y14.5 and ISO GPS

Geometric Dimensioning and Tolerancing. Two parallel systems (US ASME and international ISO) that cover the same conceptual territory with different symbols and semantics.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Y14.5 geometric characteristic symbols | 14 | ASME Y14.5-2018 Fig 3-1 | 1 | S |
| Y14.5 modifier symbols (M, L, P, F, T, ST, CF, DV, etc.) | ~25 | ASME Y14.5-2018 Tables | 1 | S |
| Y14.5 datum feature symbols + targets | ~15 | ASME Y14.5-2018 §4 | 1 | S |
| Y14.5 tolerance zone types | ~15 | ASME Y14.5-2018 §7–10 | 1 | S |
| Y14.5 feature control frame grammar rules | ~30 | ASME Y14.5-2018 §3.4 | 1 | M |
| Y14.5 material condition modifiers | 4 | ASME Y14.5-2018 §5 | 1 | S |
| Y14.41 model-based definition (MBD) tags | ~40 | ASME Y14.41-2019 | 2 | M |
| ISO 1101 geometric tolerances | ~19 | ISO 1101:2017 | 2 | S |
| ISO 5459 datums and datum systems | ~12 | ISO 5459:2011 | 2 | S |
| ISO 2768 general tolerances (linear, angular) | ~40 | ISO 2768-1, ISO 2768-2 | 2 | S |
| ISO 14405 dimensional tolerance modifiers | ~20 | ISO 14405-1:2016 | 2 | M |
| ISO 14660 extracted integral feature types | ~15 | ISO 14660-1 (superseded by 17450) | 3 | M |
| ISO 22081 general geometrical specifications | ~15 | ISO 22081:2021 | 2 | M |
| Y14.8 welding symbols | ~60 | ASME Y14.8, AWS A2.4 | 2 | S |
| Y14.36 surface texture symbols | ~40 | ASME Y14.36M, ISO 1302 | 2 | S |
| **Subtotal: GD&T** | **~364** | — | — | — |

**Demo slice:** All 14 geometric characteristic symbols + 4 material condition modifiers + 10 most-used modifiers. That's 28 rows covering 95% of typical feature control frames.

**Why this matters:** Y14.5 is the single most cited standard in regulated engineering. An AI that correctly generates feature control frames ("position Ø0.010 M A B M C" with MMC modifiers on both the feature and the datums) is immediately recognizable to any mechanical engineer as "this thing actually knows GD&T." It's a signal of seriousness.

---

### 2.5 Materials Catalogs

The largest domain by row count. Overlaps significantly with MIRA territory — and that's a feature, not a bug. If BSVoice-CAD calls `material_lookup("6Al-4V titanium")` and gets back the same canonical ID MIRA uses, the two products share a data backbone.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| ASTM steel specifications | ~2,000 | ASTM Compass (licensed) or ASTM Index | 2 | L |
| ASTM non-ferrous specifications | ~1,500 | ASTM Compass | 2 | L |
| ASTM plastics (D-series) | ~800 | ASTM Compass | 2 | L |
| AMS aerospace materials | ~3,500 | SAE AMS Index | 2 | L |
| ISO materials (various TCs) | ~2,000 | ISO Online Browsing Platform | 2 | L |
| JIS materials (Japanese) | ~1,500 | JSA catalog | 3 | L |
| DIN materials (German legacy) | ~1,200 | Beuth catalog | 3 | L |
| MIL-spec materials (superseded but still cited) | ~600 | ASSIST-Quick Search | 3 | M |
| UNS (Unified Numbering System) cross-ref | ~6,000 | SAE-ASTM DS-56K | 2 | L |
| Medical-grade materials (ASTM F-series) | ~120 | ASTM F04 Committee catalog | 1 | M |
| ISO 5832 implant materials | ~15 | ISO 5832-1 through 5832-14 | 1 | S |
| ASTM F86 / F2924 / F3001 implant-specific | ~20 | ASTM F04 catalog | 1 | S |
| FDA-recognized standards (consensus list) | ~40 | FDA CDRH recognition database | 1 | S |
| **Subtotal: Materials** | **~19,295** | — | — | — |

**Demo slice (medical-focused):**
- Titanium implant grades (ISO 5832-1 through -3): 5 rows
- Stainless steel surgical (ISO 5832-1 Grade 2, ASTM F138, ASTM F139): 5 rows
- CoCrMo (ISO 5832-4, ASTM F75, F799, F1537): 5 rows
- PEEK for spine applications (ASTM F2026): 2 rows
- UHMWPE (ASTM F648): 2 rows
- Nitinol (ASTM F2063): 3 rows
- Total demo slice: 22 rows covering 90% of implantable device materials

**Why this matters:** A Medtronic engineer dictating "6Al-4V ELI titanium per ASTM F136" expects the AI to resolve all of that to a canonical material ID, pull the typical mechanical properties, and cite the exact ASTM standard in the DHF. This is where the governed-knowledge pattern produces a defensible audit trail — no cosine similarity, no hallucination, just SQL.

---

### 2.6 Fastener Catalogs

Industry-specific part-number catalogs. Every catalog is finite, enumerable, and already published.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| MS (Military Standard, USAF/Navy legacy) | ~1,500 | DODSSP ASSIST | 2 | M |
| AN (Army-Navy, legacy but still used in GA aviation) | ~800 | ASSIST + SAE J507 | 2 | M |
| NAS (National Aerospace Standard) | ~1,500 | AIA-NAS catalog | 2 | M |
| NASM (National Aerospace Standard Metric) | ~400 | AIA-NAS catalog | 3 | M |
| ISO fasteners (2009, 4014, 4017, 4032, 4762, 7380, 10642, etc.) | ~2,500 | ISO TC 2 catalog | 2 | M |
| DIN fasteners (6914, 7984, 912, 931, 933, 934, etc.) | ~2,000 | Beuth Verlag | 2 | M |
| ANSI/ASME fasteners (B18 series) | ~1,200 | ASME B18 standards | 2 | M |
| Medical bone screws (AO/ASIF, Synthes, Stryker, Medtronic internal) | ~200 | AO Foundation + internal catalogs | 1 | L |
| Dental implant screws (ISO 14801, proprietary) | ~150 | ISO 14801 + manufacturer catalogs | 3 | L |
| Spinal pedicle screws (proprietary) | ~100 | Medtronic internal | 1 | L |
| Rivet catalogs (solid, blind, Cherry, Hi-Lok) | ~600 | AN/MS/NAS + Cherry Aerospace | 3 | M |
| Insert catalogs (Heli-Coil, Keensert, Time-Sert) | ~400 | manufacturer catalogs | 2 | M |
| **Subtotal: Fasteners** | **~11,350** | — | — | — |

**Demo slice (medical):**
- A small, illustrative set of bone screw geometries (cortical, cancellous, locking): 15 generic rows
- Plus 10 standard commercial fasteners (M3×8 SHCS, #6-32×3/8 BHCS, etc.)
- Total: 25 rows
**Note:** Demo seed uses generic representations, not proprietary Medtronic part numbers. Proprietary catalogs require a contractual relationship and are Tier 2 work conditional on a signed NDA.

---

### 2.7 Manufacturing Operations

Machining, forming, joining, finishing. These map to features the engineer wants to create and to manufacturability callouts on drawings.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Machining: turning operations | ~25 | ISO 3002, Machinery's Handbook | 1 | S |
| Machining: milling operations | ~35 | ISO 3002, Machinery's Handbook | 1 | S |
| Machining: drilling/boring/reaming | ~20 | Machinery's Handbook | 1 | S |
| Machining: grinding (surface, cylindrical, centerless, creep-feed) | ~15 | Machinery's Handbook | 2 | S |
| Machining: EDM (wire, sinker, fast-hole) | ~8 | AWS | 3 | S |
| Forming: sheet metal (bend, flange, hem, emboss, lance, form) | ~40 | ASM Handbook Vol. 14B | 1 | M |
| Forming: bulk (forging, extrusion, drawing, rolling) | ~30 | ASM Handbook Vol. 14A | 3 | M |
| Joining: welding (arc, resistance, solid-state, beam) | ~50 | AWS A3.0 | 2 | M |
| Joining: brazing and soldering | ~20 | AWS C3 | 2 | S |
| Joining: adhesive bonding | ~15 | ISO/ASTM | 2 | S |
| Joining: mechanical (fastening, riveting, press-fit) | ~25 | Machinery's Handbook | 2 | S |
| Finishing: surface treatment (anodize, passivate, plate, paint, powder) | ~60 | MIL-STD, ASTM B-series | 1 | M |
| Finishing: heat treatment (anneal, temper, quench, age, stress-relieve) | ~25 | ASM Handbook Vol. 4 | 2 | M |
| Finishing: cleaning (electropolish, passivation, pickling) | ~15 | ASTM A967, ISO 15730 | 1 | S |
| **Subtotal: Mfg Ops** | **~383** | — | — | — |

**Demo slice:** Drilling + tapping + milling top operations (20 rows) — enough to cover typical callouts like "drill and tap," "ream to size," "finish-mill the face."

---

### 2.8 Surface Finish Specifications

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Ra / Rz / Rq / Rt / Rp / Rv / Rsk / Rku roughness parameters | ~30 | ISO 4287, ISO 25178 | 2 | S |
| Surface texture symbol grammar (ISO 1302 / ASME Y14.36) | ~20 | ISO 1302:2002, Y14.36M-2018 | 2 | S |
| Lay patterns (parallel, perpendicular, circular, multidirectional, crossed, radial, particulate) | 7 | ASME Y14.36 | 2 | S |
| Typical Ra values by operation | ~60 | Machinery's Handbook Table | 1 | S |
| Electropolish/passivation surface classes | ~15 | ASTM A967, ASTM F86 | 1 | S |
| **Subtotal: Surface Finish** | **~132** | — | — | — |

---

### 2.9 Medical Device-Specific Vocabulary

Where Medtronic validation will start. Terms that regulate design intent in a way the FDA and notified bodies care about.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Catheter anatomy (shaft, tip, hub, strain relief, transition, marker band) | ~40 | ASTM F04, ISO 10555 | 1 | M |
| Catheter French sizing (1Fr through 34Fr) | 34 | ISO 10555-1 | 1 | S |
| Stent anatomy (cell, strut, crown, connector, marker, flare) | ~25 | ASTM F04, ISO 25539 | 1 | M |
| Stent design parameters (foreshortening, radial force, expansion ratio) | ~15 | ISO 25539-2, industry-standard | 1 | M |
| Guidewire anatomy (tip, transition, core, coating, radiopaque section) | ~20 | ASTM F2394, ISO 11070 | 1 | M |
| Implant fixation features (porous coating, thread, spike, fin, peg) | ~15 | AO Foundation | 1 | M |
| Orthopedic plate anatomy (holes, slots, contour, beveled edge, countersink) | ~20 | AO Foundation | 1 | M |
| Vascular access port anatomy (septum, reservoir, catheter connector, suture tab) | ~15 | ISO 10555-9 | 1 | M |
| Pacemaker/ICD can anatomy (header, feedthrough, can, coating) | ~20 | internal + FDA guidance | 1 | L |
| Ablation catheter-specific (electrode, irrigation port, thermocouple, return pad) | ~20 | FDA guidance | 1 | L |
| Neuromodulation lead anatomy (contact, spacer, fixation anchor, strain relief) | ~20 | FDA guidance | 1 | L |
| Delivery system terminology (sheath, dilator, hub, flush port, introducer) | ~25 | ISO 10555 | 1 | M |
| Suture-line anatomy (needle type, curvature, length, attachment) | ~30 | USP, ISO 15223 | 2 | M |
| Balloon anatomy (proximal cone, distal cone, waist, working length, compliance) | ~15 | ASTM F04 | 1 | M |
| Cardiac valve anatomy (frame, leaflet, skirt, commissure, paravalvular seal) | ~20 | ISO 5840 | 1 | L |
| Biocompatibility / sterilization callouts (EO, gamma, e-beam, autoclave) | ~20 | ISO 10993, ISO 11135, ISO 11137 | 1 | S |
| Packaging callouts (pouch, tray, tyvek lid, seal type) | ~25 | ASTM F88, ISO 11607 | 2 | M |
| **Subtotal: Medical Device Vocab** | **~379** | — | — | — |

**Demo slice:** Catheter anatomy (20 rows), stent anatomy (10 rows), delivery system (10 rows). These produce the most visually obvious "the AI understands medical device geometry" moments.

**Why this matters:** A Medtronic engineer saying "6Fr catheter shaft with 0.014\" guidewire lumen and radiopaque marker band at distal tip" exercises the whole stack — catheter anatomy lookup, French sizing, lumen geometry, marker band feature type. No other hackathon team will have this vocabulary in their system. **This is the moat.**

---

### 2.10 Welding / Brazing / Joining Callouts

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Weld symbols (AWS A2.4) | ~60 | AWS A2.4:2020 | 2 | S |
| Welding processes (GMAW, GTAW, SMAW, FCAW, PAW, LBW, EBW, RSW, etc.) | ~50 | AWS A3.0 | 2 | S |
| Filler metal specifications (AWS A5.x) | ~400 | AWS A5 series | 3 | L |
| Joint types (butt, lap, tee, corner, edge) | 5 | AWS A2.4 | 2 | S |
| Weld types (groove, fillet, plug, slot, spot, seam, projection, flash, upset) | ~15 | AWS A2.4 | 2 | S |
| Non-destructive testing callouts (RT, UT, MT, PT, VT, LT, AE) | ~25 | ASNT, ASME Section V | 2 | S |
| **Subtotal: Welding** | **~555** | — | — | — |

---

### 2.11 Drawing / Annotation Vocabulary

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Drawing view types (front, top, right, isometric, section, detail, auxiliary, broken) | ~20 | ASME Y14.3, ISO 128 | 2 | S |
| Section cut types (full, half, offset, revolved, removed, broken-out, aligned) | ~10 | ASME Y14.3 | 2 | S |
| Line types (visible, hidden, center, phantom, cutting plane, viewing plane, break) | ~12 | ASME Y14.2 | 2 | S |
| Title block fields (standard per ASME Y14.1) | ~30 | ASME Y14.1 | 2 | S |
| Revision block conventions | ~10 | ASME Y14.35 | 2 | S |
| Drawing sheet sizes (A–F, A0–A4, ANSI, ISO, arch) | ~20 | ASME Y14.1, ISO 5457 | 2 | S |
| Standard abbreviations (TYP, TIR, THRU, ∅, ⌀, CL, CSK, CBORE, SPOT, MAX, MIN, REF) | ~100 | ASME Y14.38 | 1 | S |
| **Subtotal: Drawings** | **~202** | — | — | — |

**Demo slice:** Top 20 abbreviations (TYP, TIR, THRU, CSK, CBORE, CL, MAX, MIN, REF, DIA, RAD, TAP, DRILL, BORE, SPHERE, THD, FLT, CHAM, COUNTER) — these appear on 99% of drawings.

---

### 2.12 Geometric Primitives and Reference Geometry

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Sketch entity types (line, arc, circle, ellipse, parabola, conic, spline, slot, polygon, text) | ~30 | SolidWorks API (overlaps 2.2) | 1 | S |
| Reference geometry types (plane, axis, point, coordinate system, surface) | ~10 | SolidWorks API | 1 | S |
| Sketch relation types (coincident, tangent, parallel, perpendicular, concentric, collinear, equal, symmetric, midpoint, fix, merge, vertical, horizontal) | ~30 | SolidWorks API | 1 | S |
| **Subtotal: Primitives** | **~70** | — | — | — |

---

### 2.13 Units and Unit Systems

Rarely discussed but a surprising source of demo failures. The agent must know that "10 mil" ≠ "10 mm" and "1 atm" ≠ "1 bar" without having to think about it.

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| SI base and derived units | ~30 | SI Brochure 9th ed | 1 | S |
| US customary length (inch, mil, thou, mile, foot, yard, nautical mile) | ~15 | NIST SP 811 | 1 | S |
| Engineering pressure (psi, ksi, bar, atm, kPa, MPa, torr, mmHg, inHg) | ~15 | NIST SP 811 | 1 | S |
| Force units (N, kN, lbf, kip, dyne) | ~10 | NIST SP 811 | 1 | S |
| Angle units (deg, rad, grad, arc-minute, arc-second) | 5 | NIST SP 811 | 1 | S |
| Unit conversion factors (complete table) | ~200 | NIST SP 811 | 1 | S |
| Tolerance class defaults by ISO / ASME | ~30 | ISO 286, ASME B4.1 | 1 | S |
| **Subtotal: Units** | **~305** | — | — | — |

---

### 2.14 Composite and Polymer Materials (Tier 1 for medical, Tier 3 overall)

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Thermoplastic polymers (ISO 1043-1, ASTM D1600) | ~200 | ISO 1043-1:2011 | 2 | M |
| Thermoset resins | ~100 | ISO 1043, manufacturer catalogs | 2 | M |
| Elastomers and TPEs | ~80 | ISO 1629, ASTM D1418 | 2 | M |
| Fiber types (carbon, glass, aramid, natural) | ~40 | ASTM D3878 | 2 | S |
| Composite layup callouts | ~30 | MIL-HDBK-17 | 3 | M |
| Biomedical polymers (PLA, PLGA, PCL, PEEK, UHMWPE, silicone grades) | ~40 | ASTM F-series, USP Class VI | 1 | M |
| **Subtotal: Polymers** | **~490** | — | — | — |

---

### 2.15 Coatings and Surface Treatments

| Domain | Est. rows | Source | Tier | Effort |
|--------|-----------|--------|------|--------|
| Anodize types (Type I, II, III, hardcoat, chromic, sulfuric, phosphoric) | ~15 | MIL-A-8625, ISO 10074 | 1 | S |
| Passivation callouts (Type 1–8 per ASTM A967) | 8 | ASTM A967 | 1 | S |
| Electroplating (Ni, Cr, Zn, Cd, Au, Ag, Sn, Cu) | ~30 | AMS 2403–2423 series | 2 | S |
| Conversion coatings (chromate, phosphate, black oxide) | ~20 | MIL-DTL-5541, MIL-DTL-16232 | 2 | S |
| PVD/CVD coatings (TiN, TiCN, TiAlN, DLC, hydroxyapatite for implants) | ~25 | various | 1 | M |
| Paint / powder coat systems | ~30 | MIL-PRF-85285, ISO 12944 | 3 | M |
| Electropolishing callouts | ~10 | ASTM B912 | 1 | S |
| **Subtotal: Coatings** | **~138** | — | — | — |

---

## 3. Corpus Totals

| Category | Rows |
|----------|-----:|
| SolidWorks API domain | 2,020 |
| Threading | 1,570 |
| GD&T | 364 |
| Materials | 19,295 |
| Fasteners | 11,350 |
| Manufacturing ops | 383 |
| Surface finish | 132 |
| Medical device vocab | 379 |
| Welding | 555 |
| Drawings | 202 |
| Primitives | 70 |
| Units | 305 |
| Polymers | 490 |
| Coatings | 138 |
| **GRAND TOTAL** | **~37,250** |

Dude's original 19K estimate was low because it didn't fully account for materials UNS cross-refs and fastener catalogs — those two alone are ~17K rows. 37K rows is still a single-developer-weeks ETL effort, not months. And the demo slice is dramatically smaller.

## 4. Demo Slice Summary

The hackathon-seed vocabulary — the rows we actually load into Postgres for the Day 6 demo:

| Category | Demo rows |
|----------|----------:|
| SolidWorks API (feature types, hole types, dim types) | 45 |
| Threads (UNC/UNF/metric common sizes) | 50 |
| GD&T (all characteristic symbols + modifiers) | 28 |
| Materials (medical implant focus) | 22 |
| Fasteners (generic bone screws + common commercial) | 25 |
| Manufacturing ops (drill/tap/mill core set) | 20 |
| Medical device vocab (catheter + stent + delivery) | 40 |
| Welding (demo-relevant subset) | 15 |
| Drawing abbreviations (top 20) | 20 |
| Units and conversions (working set) | 30 |
| **DEMO TOTAL** | **~295** |

**~300 rows. One developer-day of curation.** Enough to produce 5–10 demo moments that unambiguously show "this AI knows engineering standards, cites its sources, never hallucinates geometry."

## 5. Schema Sketch

All domains share a common base schema, with per-domain extension tables for type-specific fields.

```sql
-- Common base for all vocabulary rows
CREATE TABLE vocab.canonical_terms (
    canonical_id         TEXT PRIMARY KEY,              -- e.g., 'ASME_B1_1:6-32_UNC_2B'
    domain               TEXT NOT NULL,                  -- e.g., 'threads', 'gdnt', 'materials'
    standard_source      TEXT NOT NULL,                  -- e.g., 'ASME B1.1-2019'
    revision_year        INT NOT NULL,
    canonical_name       TEXT NOT NULL,                  -- human-readable
    superseded_by        TEXT REFERENCES vocab.canonical_terms(canonical_id),
    retired              BOOLEAN NOT NULL DEFAULT FALSE,
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_canonical_domain ON vocab.canonical_terms (domain);
CREATE INDEX idx_canonical_standard ON vocab.canonical_terms (standard_source);

-- Aliases for natural-language resolution
CREATE TABLE vocab.aliases (
    alias_id             BIGSERIAL PRIMARY KEY,
    canonical_id         TEXT NOT NULL REFERENCES vocab.canonical_terms(canonical_id),
    alias_text           TEXT NOT NULL,
    alias_type           TEXT NOT NULL CHECK (alias_type IN ('common', 'spoken', 'abbreviation', 'legacy')),
    language             TEXT NOT NULL DEFAULT 'en-US'
);

CREATE INDEX idx_alias_text ON vocab.aliases (alias_text);
CREATE INDEX idx_alias_canonical ON vocab.aliases (canonical_id);

-- Per-domain extension: threads
CREATE TABLE vocab.threads (
    canonical_id         TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    thread_series        TEXT NOT NULL,                  -- 'UN', 'UNC', 'UNF', 'M', 'BSP', 'NPT'
    nominal_size         TEXT NOT NULL,                  -- '6-32', 'M6', '1/4-20'
    major_diameter_mm    NUMERIC NOT NULL,
    pitch_mm             NUMERIC NOT NULL,
    tpi                  NUMERIC,                        -- threads per inch, null for metric
    tap_drill_nominal_mm NUMERIC,
    tolerance_class      TEXT,                           -- '2B', '6H', 'H'
    tap_drill_callout    TEXT                            -- e.g., '#36', '0.159"', '5.0 mm'
);

-- Per-domain extension: materials
CREATE TABLE vocab.materials (
    canonical_id             TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    material_class           TEXT NOT NULL,              -- 'steel', 'titanium', 'polymer', 'ceramic'
    uns_number               TEXT,                       -- 'R56400', 'S31600'
    common_grade_name        TEXT,                       -- '6Al-4V', '316L'
    density_g_per_cc         NUMERIC,
    typical_yield_mpa        NUMERIC,
    typical_uts_mpa          NUMERIC,
    typical_modulus_gpa      NUMERIC,
    biocompatibility_class   TEXT,                       -- ISO 10993 class or null
    implant_grade            BOOLEAN NOT NULL DEFAULT FALSE,
    fda_recognized           BOOLEAN NOT NULL DEFAULT FALSE
);

-- Per-domain extension: fasteners
CREATE TABLE vocab.fasteners (
    canonical_id         TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    fastener_family      TEXT NOT NULL,                  -- 'MS', 'NAS', 'ISO', 'DIN', 'ASME_B18'
    part_number          TEXT NOT NULL,
    fastener_type        TEXT NOT NULL,                  -- 'screw', 'bolt', 'nut', 'washer', 'rivet'
    head_style           TEXT,                           -- 'socket_head_cap', 'button_head', 'flat_countersunk'
    drive_type           TEXT,                           -- 'hex', 'torx', 'phillips', 'slotted'
    thread_canonical_id  TEXT REFERENCES vocab.canonical_terms(canonical_id),
    nominal_length_mm    NUMERIC,
    material_canonical_id TEXT REFERENCES vocab.canonical_terms(canonical_id),
    coating              TEXT
);

-- Per-domain extension: gdnt (GD&T symbols)
CREATE TABLE vocab.gdnt_symbols (
    canonical_id         TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    symbol_category      TEXT NOT NULL CHECK (symbol_category IN (
                             'form', 'orientation', 'location', 'runout',
                             'profile', 'modifier', 'datum', 'material_condition'
                         )),
    unicode_symbol       TEXT,                           -- actual Unicode glyph where defined
    unicode_codepoint    TEXT,                           -- e.g., 'U+232D' for ⌭
    applicable_to        TEXT[] NOT NULL,                -- ['single_feature', 'related_features']
    default_datum_refs   INT NOT NULL                   -- number of datums typical
);

-- Per-domain extension: sw_api (SolidWorks enumerations)
CREATE TABLE vocab.sw_api (
    canonical_id         TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    enum_family          TEXT NOT NULL,                  -- 'swFeatureNameID_e', 'swMateType_e'
    enum_value_name      TEXT NOT NULL,                  -- 'swFmBossExtrude'
    enum_value_int       INT,                            -- 31 (the actual COM enum value)
    sw_api_category      TEXT NOT NULL,                  -- 'feature', 'mate', 'dimension'
    sw_version_introduced TEXT,                          -- '2018', '2020'
    sw_version_deprecated TEXT
);

-- Per-domain extension: medical_device_anatomy
CREATE TABLE vocab.med_anatomy (
    canonical_id         TEXT PRIMARY KEY REFERENCES vocab.canonical_terms(canonical_id),
    device_class         TEXT NOT NULL,                  -- 'catheter', 'stent', 'lead'
    anatomical_region    TEXT NOT NULL,                  -- 'distal_tip', 'shaft', 'hub'
    parent_feature       TEXT REFERENCES vocab.canonical_terms(canonical_id),
    typical_dimensions   JSONB                           -- flexible schema per device class
);
```

The BSMS pipeline uses these tables as ground truth. Bouncer classifies intent; query planner constructs SQL against the appropriate domain table(s); Sonnet synthesizes the answer with the `standard_source` as citation. No embeddings, no cosine, no hallucination.

## 6. ETL Plan

**Phase 1 (hackathon): Demo slice seeding.**
- Target: 300 rows, curated manually from the top of each domain
- Effort: 1 developer-day
- Deliverable: seed data in migration 007 of the orchestrator DB

**Phase 2 (v1.0 Medtronic pilot): SolidWorks API + Threads + GD&T + Medical vocab.**
- Target: ~4,500 rows (full threads, full GD&T, full SW API, full medical vocab)
- Effort: ~2 developer-weeks, mostly automation
- Method:
  - SW API: reflect over `.tlb` files
  - Threads: manually ingest ASME B1.1 + ISO 261/724 (tables are short)
  - GD&T: manually ingest Y14.5 (bounded set)
  - Medical: manual curation from ASTM F-series (300 rows)

**Phase 3 (v1.5): Materials + Fasteners.**
- Target: ~30,000 rows
- Effort: ~4 developer-weeks + licensed data access (ASTM Compass, SAE)
- Partner with NASA MIRA team or license data aggregator

**Phase 4 (v2+): Remaining domains.**
- Welding filler metals, composite layups, polymer catalogs, coating systems
- Driven by customer demand

## 7. Licensing Note

Most standards bodies (ASME, ISO, ASTM, SAE, AWS) license their standards content. Ingestion must comply with each body's terms. Options:

- **Enumerations and structural grammar** (symbol lists, feature control frame grammar, enum codes): generally reproducible as factual information; not copyrightable
- **Tabular data** (thread dimensions, material properties): licensed but fair-use arguments apply for small factual extracts; contract ASTM Compass or SAE Digital Library for full access
- **Prose descriptions**: strictly licensed; do NOT ingest

BoundState legal review required before Phase 2 ingestion begins. Jerry Gabig and Christopher Lockwood should opine on each source's licensing model.

## 8. Governance

This corpus is BoundState-owned intellectual property: a curated, citable, queryable engineering vocabulary. Same IP model as MIRA's Authority Packs. Customers pay for the license; BoundState maintains the ingestion pipeline and updates as standards revise.

Every row carries `standard_source` + `revision_year` so an audit of any BSVoice-CAD output can trace back to the exact version of the standard cited. When ASME Y14.5 publishes a new revision (Y14.5-2018 → Y14.5-202X), the canonical IDs remain stable; superseded rows are marked via `superseded_by` and `retired = true`. Existing DHF entries continue to resolve via their recorded canonical_id + revision_year.

## 9. Next Steps

1. **Do not wait for Oracle2.** Corpus scoping does not depend on the 5-spec foundation.
2. **Produce the demo-slice seed data.** Pure curation work — 300 rows across the categories in §4. One developer-day.
3. **After Oracle2 returns:** write SPEC-07 "CAD Vocabulary Layer" referencing this corpus and specifying the BSMS pipeline details (bouncer persona, query planner, Sonnet synthesis, citation format).
4. **Integrate SPEC-07 seed migration into the orchestrator startup sequence** as migration 007.
5. **Wire SPEC-05 agent loop** (when written) to call the vocabulary lookup as a first-class path BEFORE constructing tool calls.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC.*
*This corpus manifest is Proprietary; the SolidWorks voice copilot code ships under MIT.*
