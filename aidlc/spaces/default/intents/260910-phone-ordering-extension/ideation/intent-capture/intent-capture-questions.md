# Intent Capture & Framing — Questions

## Sources

- [desc] Initial description: "Reverse-engineer this codebase — an AI voice agent that holds real-time phone conversations over Twilio using the Gemini Live API and executes configured tasks autonomously. Focus on the call handling loop, context management, and tool execution paths. I want to extend it with a live phone-ordering workflow: structured item capture during the call, validation against a configured catalog, and spoken confirmation before an order is committed. Produce requirements, architecture, and unit-of-work artifacts from the existing code, scoped toward that extension."
- [scope] Workflow-selected scope: `phone-ordering-reverse-engineering-design`.

## Q1. What business problem is the phone-ordering extension solving?

The description names the mechanism (structured item capture, catalog validation, spoken confirmation) but not the underlying business driver.

[Answer]:
A. Replace or augment a human order-taker so calls can be handled without staff availability
B. Add a new channel (phone) to an existing ordering system that today only takes orders via web/app/in-person
C. Reduce order errors/mishears from a currently unstructured or ad-hoc phone-order process
D. Not yet defined
X. Other (please specify)

## Q2. Who is the customer/caller for this phone-ordering workflow, and what pain are they experiencing today?

[Answer]:
A. External end customers calling a business (e.g. restaurant, retailer) to place an order — today's pain is wait times, mishears, or no phone option at all
B. Internal staff/dispatch placing orders on behalf of customers or other locations
C. Not yet defined
X. Other (please specify)

## Q3. What does success look like for this extension, and what metrics matter?

[Answer]:
A. Order accuracy — percentage of orders committed exactly as spoken, no item/quantity errors
B. Call completion rate — percentage of ordering calls that reach a confirmed, committed order without human handoff
C. Speed — average time from call start to order confirmation
D. Not yet defined / no specific metric target yet
X. Other (please specify)

## Q4. What is the trigger for building this now?

[Answer]:
A. This is a personal/side project and the trigger is technical interest in extending the existing voice-agent capability
B. A specific business or client has asked for phone-ordering support
C. Not yet defined
X. Other (please specify)

## Q5. Who are the stakeholders for this extension, and is this a solo effort or does it involve other decision-makers?

[Answer]:
A. Solo project — I am the sole stakeholder, developer, and decision-maker
B. There is a business owner or client stakeholder I need to satisfy in addition to myself
C. Not yet defined
X. Other (please specify)

## Q6. What does the "configured catalog" look like today, or how do you expect to define it?

This shapes what "validation against a configured catalog" means architecturally — catalogs can range from a static file to a live inventory system.

[Answer]:
A. A catalog does not exist yet — it will be newly authored (e.g. a config file or simple database table) as part of this work
B. There is an existing menu/catalog source (e.g. a file, spreadsheet, or database) that should be reused
C. Not yet defined
X. Other (please specify)

## Q7. What should happen after an order is confirmed and "committed" — where does the committed order go?

The description stops the requested deliverable at requirements/architecture/units (no code will be written in this workflow), but the target design still needs to know the intended destination for a committed order so the architecture can name an integration boundary correctly, even if unimplemented.

[Answer]:
A. Persisted locally (e.g. a database or log) for later retrieval — no external system integration needed yet
B. Sent to an existing external system (e.g. POS, order-management system, notification/email) — to be named later
C. Not yet defined
X. Other (please specify)

## Q8. The workflow was started with the scope `phone-ordering-reverse-engineering-design` (reverse-engineer the call/context/tool-execution architecture, then produce requirements, architecture, and unit-of-work artifacts for the extension — explicitly stopping before any code is written). Does that match your intended boundary for this piece of work?

[Answer]:
A. Yes, confirm — I want design artifacts only, no code in this workflow
B. No — I actually want code written too, not just design artifacts
X. Other (please specify)
