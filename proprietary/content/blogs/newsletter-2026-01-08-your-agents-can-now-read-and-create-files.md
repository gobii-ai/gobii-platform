---
title: "AI Document Automation: Read, Transform, and Create Files"
date: 2026-01-08
updated: 2026-07-20
description: "Learn AI document automation through a seven-route file workflow that reads PDFs and CSVs, transforms content, and returns review-ready files for teams."
author: "Matt Greathouse"
author_type: "Person"
author_url: "/team/"
author_job_title: "Engineering"
author_bio: "Matt Greathouse is a full-stack engineer at Gobii focused on the secure, reliable infrastructure behind persistent browser-native AI agents."
author_same_as:
  - "https://www.linkedin.com/in/matt-greathouse/"
  - "https://github.com/matt-greathouse"
seo_title: "AI Document Automation: Read, Transform, Create Files"
seo_description: "Learn AI document automation through a seven-route file workflow that reads PDFs and CSVs, transforms content, and returns review-ready files for teams."
canonical: "https://gobii.ai/blog/newsletter-2026-01-08-your-agents-can-now-read-and-create-files/"
slug: "newsletter-2026-01-08-your-agents-can-now-read-and-create-files"
image: "/static/images/blog/newsletters/newsletter-2026-01-08-ai-document-automation-og.webp"
image_alt: "Gobii agent turning PDFs, spreadsheets, images, and documents into finished files beside a Put Files to Work call to action"
og_image_alt: "AI Document Automation headline, Gobii mascot, file workflow, and Put Files to Work call to action"
image_width: 1200
image_height: 630
schema_graph: true
keywords:
  - AI document automation
  - automated document processing
  - AI document processing
  - document workflow automation
  - PDF automation
faq:
  - question: "What is AI document automation?"
    answer: >-
      AI document automation uses software to read document content, organize or transform it, and produce a defined output with less manual handling. A complete workflow includes the file source, extraction or interpretation method, business rules, validation checks, destination, and a human review point when errors carry meaningful consequences.
  - question: "Which files can Gobii agents read and create?"
    answer: >-
      Gobii's original bidirectional attachment release covered PDFs, CSVs, images, and office documents. Actual success depends on file quality, size, layout, password protection, and the requested output. Start with one representative file and a precise output contract before expanding a workflow to larger batches.
  - question: "How can I send a file to a Gobii?"
    answer: >-
      Gobii currently documents seven file routes: chat, email, SMS or MMS, the file manager, agent-created files, peer handoffs, and Remote MCP or supported integrations. Use a chat attachment for one request and filespace for inputs or outputs that should remain available by stable name or path.
  - question: "Can AI document automation replace human review?"
    answer: >-
      Not for every workflow. Human review should remain where a wrong extraction, ranking, calculation, disclosure, or external message could affect money, employment, compliance, or customer trust. Low-risk transformations can use spot checks, while consequential outputs need defined owners, tolerances, source references, and approval before release.
  - question: "What is the difference between OCR and AI document automation?"
    answer: >-
      OCR converts visible characters into machine-readable text. AI document automation can use that text plus layout, images, instructions, tools, and business rules to classify a document, extract fields, compare records, create a new file, or route an exception. OCR is one possible input step, not the entire workflow.
tags:
  - newsletter
  - weekly
  - product-updates
  - document-automation
  - AI-agents
---

<img src="/static/images/blog/newsletters/newsletter-2026-01-08-ai-document-automation-og.webp" alt="Gobii agent turning PDFs, spreadsheets, images, and documents into finished files beside a Put Files to Work call to action" width="1200" height="630" loading="eager" decoding="async" fetchpriority="high" style="max-width: 100%; height: auto; border-radius: 10px;">

**AI document automation** turns a file into a repeatable work product. The useful unit is not "read this PDF." It is the whole path: accept the right file, extract or interpret its contents, apply business rules, create the required output, preserve the evidence, and route uncertain results to a person. Gobii supports that broader read-transform-return loop. An agent can receive PDFs, CSVs, images, and office documents, work with their content, then return a new file instead of stopping at a chat answer.

In a July 20, 2026 U.S. query, the [DataForSEO Labs Google Keyword Overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/) reported 90 monthly searches and keyword difficulty 11 for "AI document automation." The adjacent "AI document processing" term had 260 monthly searches and difficulty 33. Searchers want more than a product list. They need to know what gets automated, where OCR fits, how files move through the system, and which checks keep the output usable.

> **Key takeaways**
>
> - Gobii documents seven ways files can enter an agent workspace.
> - A reliable workflow defines the input, transformation, output, checks, and exception path.
> - OCR can recover text, but layout, reasoning, policy, and validation still matter.
> - Review consequential files before they leave the workspace.

[Put your files to work with Gobii](https://gobii.ai/accounts/signup/?utm_source=blog&utm_medium=web&utm_campaign=20260108-document-automation&utm_content=hero-cta)

**In this guide**

- [AI document automation explained](#what-is-ai-document-automation)
- [The Gobii file workflow](#how-does-ai-document-automation-work-in-gobii)
- [Supported inputs and outputs](#what-files-can-an-ai-agent-read-and-create)
- [18 workflow examples](#18-practical-ai-document-automation-workflows)
- [A prompt blueprint](#how-should-you-prompt-an-ai-document-workflow)
- [OCR, document understanding, and agents](#where-does-ocr-end-and-document-automation-begin)
- [Validation](#how-do-you-validate-ai-generated-documents)
- [Security and governance](#security-and-governance-for-document-workflows)
- [Frequently asked questions](#frequently-asked-questions)

<details>
  <summary><strong>Keyword research method</strong></summary>
  <p>We queried DataForSEO Labs Google Keyword Overview on July 20, 2026 with <code>location_name: United States</code>, <code>language_code: en</code>, and clickstream normalization disabled. DataForSEO last updated the two cited keyword records on July 15 and July 14, 2026. Volumes are rounded estimates, not traffic forecasts.</p>
</details>

In the demonstration below, watch for the transferable pattern: extraction is only one step, and the agent still needs a defined workflow, destination, and review boundary around it.

<figure class="video-embed" style="margin: 2.5rem 0; text-align: center;">
  <div style="position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; max-width: 100%; border-radius: 12px;">
    <iframe
      srcdoc="<style>*{padding:0;margin:0;overflow:hidden}html,body{height:100%}img,span{position:absolute;width:100%;top:0;bottom:0;margin:auto}span{height:1.5em;text-align:center;font:48px/1.5 sans-serif;color:white;text-shadow:0 0 0.5em black}</style><a href='https://www.youtube.com/embed/crHR6sEnTpE?autoplay=1'><img src='https://img.youtube.com/vi/crHR6sEnTpE/hqdefault.jpg' alt='Build AI agents for fast, high-volume document automation in Copilot Studio'><span>&#x25BA;</span></a>"
      style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
      loading="lazy"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      allowfullscreen
      title="Build AI agents for fast, high-volume document automation in Copilot Studio"
      aria-label="YouTube video: Build AI agents for fast, high-volume document automation in Copilot Studio">
    </iframe>
  </div>
  <figcaption><a href="https://www.youtube.com/watch?v=crHR6sEnTpE">Build AI agents for fast, high-volume document automation in Copilot Studio</a> by Microsoft Developer shows how document extraction becomes one part of an agent workflow.</figcaption>
  <noscript>
    <p><strong>Video:</strong> <a href="https://www.youtube.com/watch?v=crHR6sEnTpE">Build AI agents for fast, high-volume document automation in Copilot Studio</a> by Microsoft Developer. The 18-minute session demonstrates an agent-oriented document automation workflow.</p>
  </noscript>
</figure>

## What Is AI Document Automation?

**AI document automation is a controlled pipeline that converts files into decisions, records, or new deliverables.** In 2026, Google describes Document AI as turning unstructured documents into structured data, while its processor catalog covers OCR, classification, splitting, parsing, and analysis ([Google Cloud, Document AI documentation](https://docs.cloud.google.com/document-ai/docs), retrieved July 20, 2026). Those functions become automation only after you add workflow rules and a destination.

The phrase covers several jobs that people sometimes collapse into one:

| Layer | Question it answers | Typical result |
| --- | --- | --- |
| Ingestion | Where did the file come from? | A chat attachment, emailed PDF, workspace file, or integration handoff |
| Reading | What text, tables, fields, layout, or images are present? | Extracted text, detected fields, or visual observations |
| Transformation | What should change? | Cleaned rows, a comparison, summary, classification, or rewritten document |
| Validation | What must be true before completion? | Required fields, reconciled totals, citations, tolerances, or spot checks |
| Delivery | Where does the result go? | A new PDF, CSV, spreadsheet, report, message, or review queue |

Traditional document automation often begins with a fixed template: place these values in these fields. Intelligent document processing adds machine learning to classification and extraction. An AI agent can extend the chain by using tools, performing research, following a plan, creating a deliverable, and asking for help when a rule cannot be satisfied. That distinction matters. An extracted invoice total has little value if the workflow never checks the vendor, reconciles the line items, or puts the result where finance can review it.

## How Does AI Document Automation Work in Gobii?

**Gobii connects seven documented file-entry routes to one persistent workspace.** The current Files and Workspaces guide lists chat, email, SMS or MMS, the file manager, agent-created files, peer handoffs, and Remote MCP or supported integrations ([Gobii, Files and Workspaces](https://docs.gobii.ai/using-gobii/files-and-workspaces), retrieved July 20, 2026). That makes a file available to a longer-running job instead of trapping it in one prompt.

The practical loop has five stages:

1. **Receive.** Attach a file in chat, forward it by email, upload it to filespace, or pass it through an approved connection.
2. **Identify.** Name the source file and the job. If several versions exist, specify the exact path or date.
3. **Work.** Ask the agent to extract, compare, calculate, research, classify, rewrite, or combine the content using its available tools.
4. **Check.** Require source references, totals, mandatory fields, exceptions, or a comparison with a known-good record.
5. **Return and retain.** Create the requested file, summarize what changed in the timeline, and keep reusable inputs or outputs in filespace.

<img src="/static/images/blog/newsletters/newsletter-2026-01-08-gobii-files-workspace.webp" alt="Gobii filespace showing reusable source CSV files beside an agent conversation and timeline" width="1280" height="889" loading="lazy" decoding="async" style="max-width: 100%; height: auto; border-radius: 10px;">

<!-- [PERSONAL EXPERIENCE] -->

When we built file support into persistent agents, the hard part was not adding an upload control. The real requirement was continuity. A useful source file needs a stable home, the agent needs to distinguish it from stale versions, and collaborators need a traceable output after the conversation scrolls away. Filespace supplies that working boundary. Use chat attachments for a single request. Move durable reference material, recurring inputs, and finished deliverables into clearly named folders.

For workflows that begin in a browser, [Browser Intelligence](/blog/newsletter-2026-06-09-browser-intelligence/) can preserve screenshots and downloads before the file-processing stage. When one specialist needs to hand a file to another, [Agent Peer File Sharing](/blog/newsletter-2026-03-24-let-your-agents-pass-the-baton/) keeps the artifact attached to the handoff rather than forcing a manual download and re-upload.

## What Files Can an AI Agent Read and Create?

**The January 2026 release covered four broad input families: PDFs, CSVs, images, and office documents.** Current results still depend on layout, scan quality, password protection, size, and the requested operation. The 2025 DocBench benchmark used 229 real documents and 1,102 questions across five domains because raw-file reading includes text, tables, figures, metadata, and unanswerable requests, not just plain text ([ACL, DocBench](https://aclanthology.org/2025.knowledgenlp-1.29/), retrieved July 20, 2026).

Use the file type as a starting point, then define what the agent should preserve:

| Input | Good first task | Useful output | Review focus |
| --- | --- | --- | --- |
| Text PDF | Extract named fields and cite page numbers | CSV, brief, or comparison table | Missing sections, wrong field mapping, unsupported conclusions |
| Scanned PDF or image | Describe visible content and recover key details where readable | Structured notes or exception list | OCR errors, handwriting, rotation, low contrast, charts |
| CSV | Normalize columns, deduplicate rows, calculate summaries | Clean CSV or analysis report | Types, row counts, formulas, dropped records, encoding |
| Office document | Compare versions, summarize clauses, apply a template | Revised document or change log | Formatting loss, comments, tracked changes, embedded objects |
| Mixed file set | Reconcile facts across documents | Source-linked dossier or matrix | Contradictions, file versions, incomplete evidence |

Do not infer support from a file extension alone. A PDF may contain selectable text, scanned pages, complex tables, signatures, or all four. A CSV may use an unusual delimiter or carry dates that look like integers. An office file may include macros, comments, or embedded sheets. Start with a representative sample, state the expected output, and make ambiguity visible instead of asking the agent to "process everything."

When the deliverable needs to remain collaborative, [Google Sheets automation](/blog/newsletter-2025-09-23-gobii-now-plays-nice-with-google-sheets/) can put validated rows into a selected workbook. A disposable export belongs in filespace. The destination should follow the next reviewer, not the tool that happened to create the data.

## 18 Practical AI Document Automation Workflows

**The strongest document workflows pair a stable method with changing files.** Google tested its document models against text, forms, receipts, document questions, layout, and other tasks, while DocBench spans academia, finance, government, law, and news ([Google Cloud, Document AI](https://docs.cloud.google.com/document-ai/docs), 2026; [ACL, DocBench](https://aclanthology.org/2025.knowledgenlp-1.29/), 2025). That range is a reminder to define one reviewable output at a time.

### Recruiting

1. **Resume screening:** PDF resumes to a criteria-linked CSV. Keep final candidate decisions with a recruiter.
2. **Candidate briefs:** Combine the job description and resume into a one-page interview brief. Require evidence for every claim so an interviewer can trace it back to the source.
3. **Offer review:** Offer PDF to a terms table that flags missing or inconsistent fields without giving legal advice.
4. **Interview synthesis:** Notes from several interviewers to a structured summary that preserves disagreement.
5. **Portfolio review:** Portfolio files to a skill matrix with source pages or artifact names.

### Sales and research

6. **Annual-report research:** A 10-K or annual report to a source-linked account brief.
7. **Stakeholder research:** Approved profiles and notes to a deduplicated contact CSV.
8. **Competitor analysis:** Product PDFs and captured pages to a dated comparison matrix.
9. **Market research:** Whitepapers to a one-page evidence brief with publication dates and methodology notes.
10. **Transcript analysis:** Interview or call transcripts to a theme table with supporting excerpts.
11. **CRM cleanup:** Exported CSV to normalized values, duplicate candidates, and a separate exception file.

### Finance and operations

12. **Invoice intake:** Invoice PDF to a review table containing vendor, date, amount, line items, and exceptions.
13. **Expense reconciliation:** Match receipts against a card export, then send missing, duplicate, or uncertain charges to a separate exceptions CSV.
14. **Policy comparison:** Two policy versions to a change log that identifies added, removed, and altered clauses.
15. **Recurring reporting:** Source spreadsheets and narrative notes to a draft report with reconciled totals.

### Content and customer work

16. **Content repackaging:** Approved research documents to a brief, outline, and source ledger.
17. **Support case synthesis:** Reconstruct a timeline from the ticket export and attachments. Keep unresolved questions separate from established facts.
18. **Customer feedback analysis:** Survey CSVs and interview notes to a theme matrix with counts and representative evidence.

<!-- [UNIQUE INSIGHT] -->

The file type is rarely the real workflow. "PDF to CSV" sounds specific, but it omits the business contract. Which fields? How should duplicates be handled? What happens when a value is absent? Who owns the final decision? The durable automation lives in those rules. [AI agent workflows](/blog/newsletter-2025-11-18-inspiration-for-your-next-agent/) explains how to combine a trigger, context, action, check, and delivery channel around that contract.

## How Should You Prompt an AI Document Workflow?

**A good document prompt names at least five things: input, task, schema, checks, and exception behavior.** Google publishes separate limits for online and batch processors, including 15-page synchronous and 500-page batch ceilings for one Enterprise OCR configuration ([Google Cloud, processor list](https://docs.cloud.google.com/document-ai/docs/processors-list), retrieved July 20, 2026). Those are Google limits, not Gobii limits, but they show why scale and execution mode must be explicit.

Use this prompt structure:

> Use `/sources/q2-vendor-invoices/` as the input. For every readable invoice, extract vendor name, invoice number, invoice date, currency, subtotal, tax, and total. Return `/outputs/q2-invoice-review.csv`. Preserve one row per invoice. Confirm subtotal plus tax equals total within $0.02. Put unreadable, duplicate, or inconsistent documents in `/outputs/q2-invoice-exceptions.csv` with the filename and reason. Do not send or post anything. Summarize counts and unresolved issues in the timeline.

Why does this work? It replaces a vague outcome with a testable contract:

- **Input boundary:** one named folder, not the whole workspace.
- **Output schema:** eight specific columns and two files.
- **Reconciliation:** a numerical tolerance rather than "check the math."
- **Exception path:** no silent guessing when a document cannot be read.
- **Action boundary:** files may be created, but nothing leaves the workspace.

For a one-off job, put those rules in chat. If the same logic recurs, let [automatic Agent Skills](/blog/newsletter-2026-03-03-your-agent-just-learned-a-new-trick/) preserve the stable procedure while file contents stay fresh. Larger jobs can benefit from [visible agent planning](/blog/newsletter-2026-05-05-agent-planning/) so you can inspect the extraction, validation, and delivery stages before the agent commits too much work.

## Where Does OCR End and Document Automation Begin?

**OCR recovers characters; document automation completes a job around those characters.** The DocVQA dataset contains 50,000 questions across more than 12,000 document images, and its original baselines remained well below 94.36% human accuracy when structure mattered ([Mathew, Karatzas, and Jawahar, DocVQA](https://arxiv.org/abs/2007.00398), retrieved July 20, 2026). Reading order, tables, labels, and visual relationships therefore belong in the workflow design.

Think of the stack this way:

| Capability | Example | What can still go wrong |
| --- | --- | --- |
| OCR | Convert a scanned invoice into text | Digits, punctuation, columns, or reading order may be wrong |
| Layout understanding | Associate labels with values and preserve tables | Unusual templates or nested sections can confuse relationships |
| Document reasoning | Compare clauses or answer a question across pages | The answer may omit evidence or overstate an inference |
| Agent workflow | Research a missing value, create a file, and route an exception | Wrong tools, stale sources, unsafe actions, or weak checks can spoil the result |

A workflow should expose the layer that failed. If the amount was read incorrectly, fix the scan or extraction. If the amount was read correctly but mapped to the wrong field, fix the schema. If fields are right but the final total is wrong, fix the rule or calculation. If the file is correct but sent to the wrong place, fix the action boundary. "The AI got it wrong" is not a useful diagnosis.

## How Do You Validate AI-Generated Documents?

**Validation should test the deliverable, not the fluency of the explanation.** DocBench evaluates 1,102 questions across text-only, multimodal, metadata, and unanswerable categories because a polished answer can still miss a table, invent a value, or ignore that the file lacks the requested fact ([ACL, DocBench](https://aclanthology.org/2025.knowledgenlp-1.29/), retrieved July 20, 2026).

Match the check to the output:

| Output | Minimum validation |
| --- | --- |
| Extracted CSV | Required columns, row count, type checks, duplicate policy, sample against source pages |
| Financial table | Reconcile subtotals, taxes, totals, currency, sign, and reporting period |
| Summary | Link each material claim to a page, section, row, or named source file |
| Comparison | Confirm both versions, effective dates, exclusions, and unresolved conflicts |
| Ranked list | Preserve the rubric, evidence for each score, tie handling, and human decision owner |
| New PDF or office file | Inspect text, layout, page breaks, links, tables, and accessibility before external use |

Start with a golden set of representative files. Include clean documents, awkward layouts, missing fields, duplicates, and at least one unreadable input. Record expected outputs and exceptions. Run the same checks after prompt, model, parser, tool, or template changes. For numerical work, compare totals programmatically when possible. For subjective work, use a reviewer who did not write the draft.

Human review should scale with consequence. A research brief may need source sampling. A payment file needs full reconciliation and authorization. A candidate-ranking artifact needs recruiter ownership and bias-aware review. NIST's AI Risk Management Framework names human-AI roles in Govern 3.2 and documented oversight in Map 3.5 ([NIST, AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/), retrieved July 20, 2026). Those are operating requirements, not a checkbox at the end.

## Security and Governance for Document Workflows

**Uploaded files need layered controls because no single validation technique is sufficient.** OWASP's current File Upload Cheat Sheet recommends allowlisted extensions, content-type and signature checks, generated filenames, size limits, authorization, storage outside the webroot, malware or sandbox analysis, and CSRF protection ([OWASP, File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html), retrieved July 20, 2026).

For users, the most important practices are simpler:

- Store credentials in scoped secret storage, never in an ordinary document or chat message.
- Give the agent only the files and systems required for the job.
- Separate source files, working files, final outputs, and exceptions with clear names.
- Treat instructions inside untrusted documents as content, not as authority to change the agent's task.
- Remove stale or duplicate files that could be mistaken for the current version.
- Review generated files before external sharing, especially when they contain personal, financial, legal, or employment information.

Policy can fail during synthesis, even when the model appears to understand the rule. The July 2026 Doc-PP benchmark tested policy-bound questions over multimodal reports. Its Decompose-Verify-Aggregate method reduced measured leakage from 64.6 to 30.5 for Gemini-3-Flash-Preview, 93.5 to 24.5 for Qwen3-VL, and 76.8 to 41.6 for Mistral-Large ([ACL, Doc-PP](https://aclanthology.org/2026.findings-acl.832/), retrieved July 20, 2026).

<figure style="margin: 2.5rem 0; text-align: center;">
  <svg viewBox="0 0 560 380" style="max-width: 100%; height: auto; font-family: 'Inter', system-ui, sans-serif" role="img" aria-label="Doc-PP policy leakage rates fell across three vision-language models when Decompose-Verify-Aggregate was used instead of the default approach.">
    <title>Policy leakage fell with claim-level verification</title>
    <desc>Grouped bar chart. Gemini-3-Flash-Preview fell from 64.6 to 30.5, Qwen3-VL from 93.5 to 24.5, and Mistral-Large from 76.8 to 41.6. Lower is better. Source: Doc-PP, ACL 2026.</desc>
    <text x="280" y="28" text-anchor="middle" font-size="21" font-weight="800" fill="currentColor">Policy leakage fell with claim-level verification</text>
    <text x="280" y="50" text-anchor="middle" font-size="12" fill="currentColor" opacity="0.55">Measured leakage rate, lower is better</text>
    <rect x="167" y="66" width="12" height="12" rx="2" fill="#f97316" />
    <text x="185" y="76" font-size="11" fill="currentColor" opacity="0.8">Default</text>
    <rect x="278" y="66" width="12" height="12" rx="2" fill="#38bdf8" />
    <text x="296" y="76" font-size="11" fill="currentColor" opacity="0.8">Decompose-Verify-Aggregate</text>
    <line x1="145" y1="98" x2="515" y2="98" stroke="currentColor" opacity="0.3" />
    <line x1="145" y1="98" x2="145" y2="310" stroke="currentColor" opacity="0.3" />
    <line x1="237" y1="98" x2="237" y2="310" stroke="currentColor" opacity="0.08" />
    <line x1="330" y1="98" x2="330" y2="310" stroke="currentColor" opacity="0.08" />
    <line x1="422" y1="98" x2="422" y2="310" stroke="currentColor" opacity="0.08" />
    <line x1="515" y1="98" x2="515" y2="310" stroke="currentColor" opacity="0.08" />
    <text x="145" y="92" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.55">0</text>
    <text x="237" y="92" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.55">25</text>
    <text x="330" y="92" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.55">50</text>
    <text x="422" y="92" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.55">75</text>
    <text x="515" y="92" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.55">100</text>
    <text x="134" y="144" text-anchor="end" font-size="11" fill="currentColor" opacity="0.8">Gemini 3 Flash</text>
    <rect x="145" y="119" width="239" height="22" rx="5" fill="#f97316" />
    <text x="376" y="135" text-anchor="end" font-size="11" font-weight="800" fill="white">64.6</text>
    <rect x="145" y="147" width="113" height="22" rx="5" fill="#38bdf8" />
    <text x="250" y="163" text-anchor="end" font-size="11" font-weight="800" fill="white">30.5</text>
    <text x="134" y="214" text-anchor="end" font-size="11" fill="currentColor" opacity="0.8">Qwen3-VL</text>
    <rect x="145" y="189" width="346" height="22" rx="5" fill="#f97316" />
    <text x="483" y="205" text-anchor="end" font-size="11" font-weight="800" fill="white">93.5</text>
    <rect x="145" y="217" width="91" height="22" rx="5" fill="#38bdf8" />
    <text x="228" y="233" text-anchor="end" font-size="11" font-weight="800" fill="white">24.5</text>
    <text x="134" y="284" text-anchor="end" font-size="11" fill="currentColor" opacity="0.8">Mistral Large</text>
    <rect x="145" y="259" width="284" height="22" rx="5" fill="#f97316" />
    <text x="421" y="275" text-anchor="end" font-size="11" font-weight="800" fill="white">76.8</text>
    <rect x="145" y="287" width="154" height="22" rx="5" fill="#38bdf8" />
    <text x="291" y="303" text-anchor="end" font-size="11" font-weight="800" fill="white">41.6</text>
    <text x="280" y="344" text-anchor="middle" font-size="11" fill="currentColor" opacity="0.65">Verification was applied to individual claims before the final answer was assembled.</text>
    <text x="280" y="369" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.35">Source: Doc-PP, ACL 2026, Table 5</text>
  </svg>
  <figcaption>Doc-PP found that checking atomic claims before aggregation reduced policy leakage across all three evaluated models. The benchmark concerns multimodal question answering, not Gobii product performance.</figcaption>
</figure>

<!-- [UNIQUE INSIGHT] -->

The operational lesson is to separate transformation from release. Let the agent extract and draft inside the workspace. Validate fields, totals, evidence, and policy against the intended recipient. Then approve the external handoff. Gobii's [production sandboxing model](/blog/how-we-sandbox-ai-agents-in-production/) describes the runtime boundary, while [one-click integrations](/blog/newsletter-2026-03-17-one-click-integrations-for-your-agents/) explains how provider scopes and selected resources narrow what an agent can reach.

## Frequently Asked Questions

**Document reading remains an evaluation problem, not a solved checkbox.** DocBench's 2025 test set spans 229 files, 1,102 questions, five domains, and four question categories, including requests that cannot be answered from the document ([ACL, DocBench](https://aclanthology.org/2025.knowledgenlp-1.29/), retrieved July 20, 2026). These answers set practical boundaries for production workflows.

### What is AI document automation?

AI document automation uses software to read document content, organize or transform it, and produce a defined output with less manual handling. A complete workflow includes the file source, extraction or interpretation method, business rules, validation checks, destination, and a human review point when errors carry meaningful consequences.

### Which files can Gobii agents read and create?

Gobii's original bidirectional attachment release covered PDFs, CSVs, images, and office documents. Actual success depends on file quality, size, layout, password protection, and the requested output. Start with one representative file and a precise output contract before expanding a workflow to larger batches.

### How can I send a file to a Gobii?

Gobii currently documents seven file routes: chat, email, SMS or MMS, the file manager, agent-created files, peer handoffs, and Remote MCP or supported integrations. Use a chat attachment for one request and filespace for inputs or outputs that should remain available by stable name or path.

### Can AI document automation replace human review?

Not for every workflow. Human review should remain where a wrong extraction, ranking, calculation, disclosure, or external message could affect money, employment, compliance, or customer trust. Low-risk transformations can use spot checks, while consequential outputs need defined owners, tolerances, source references, and approval before release.

### What is the difference between OCR and AI document automation?

OCR converts visible characters into machine-readable text. AI document automation can use that text plus layout, images, instructions, tools, and business rules to classify a document, extract fields, compare records, create a new file, or route an exception. OCR is one possible input step, not the entire workflow.

## Put Your Files to Work

**The search opportunity is attainable because "AI document automation" measured 90 monthly U.S. searches at difficulty 11 in our July 2026 [DataForSEO snapshot](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/).** The product opportunity is more concrete: Gobii can take files through seven documented routes, use them in persistent work, and return finished artifacts. The value comes from a precise contract and a reviewable result, not the upload itself.

Choose one bounded workflow. Pick a representative file, name the output fields or sections, add two checks, and state what the agent should do when information is missing. Keep the first result inside the workspace. Compare it with the source, tighten the rules, and only then expand the volume or destination. If the work repeats, preserve the method while the source files and business facts remain fresh.

**Related workflows:** [Design a persistent AI agent workflow](/blog/newsletter-2025-11-18-inspiration-for-your-next-agent/), [move files between specialist agents](/blog/newsletter-2026-03-24-let-your-agents-pass-the-baton/), or [send validated rows to Google Sheets](/blog/newsletter-2025-09-23-gobii-now-plays-nice-with-google-sheets/).

[Start an AI document workflow](https://gobii.ai/accounts/signup/?utm_source=blog&utm_medium=web&utm_campaign=20260108-document-automation&utm_content=final-cta)
