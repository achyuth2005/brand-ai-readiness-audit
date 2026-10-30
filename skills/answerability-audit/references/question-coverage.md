# Question Coverage

## Mechanism

User prompts are question-shaped. When the retrievable unit on a page is also question-shaped —
a heading phrased as the question, with the answer directly beneath it — the passage selected
for a query is the one that contains its answer. When the page is organised around the brand's
own internal vocabulary instead, retrieval has to guess which passage answers the question, and
the question and its answer often land in different chunks.

## Query shapes and the content shapes that answer them

The most useful proactive advice follows from this mapping, and every row is something a site
either has or visibly lacks.

| Query shape users type | Content shape that answers it | Where this marketplace checks |
| :--- | :--- | :--- |
| "What is <brand>?" | one plain definitional sentence near the top | `G3.ANSW.NO_DEFINITIONAL_SENTENCE` |
| "How much does <brand> cost?" | prices or ranges as text on a stable pricing URL | `saas_software` playbook recommendation; `G1.NONTEXT.FACT_LOCKED_IN_MEDIA` for prices in images |
| "<brand> vs <competitor>" | an honest comparison page, in a table | comparison playbook recommendation |
| "How do I <task>?" | a question heading with numbered steps | `G3.ANSW.KEY_FACTS_UNLISTED`, `ANSWER_BURIED` |
| "Does <product> support <feature>?" | a specification table and a dated changelog | `G3.ANSW.KEY_FACTS_UNLISTED`, changelog recommendation |
| "Is <brand> open now / where is it?" | hours and address as text on every page | `local_business` playbook; `G1.NONTEXT.FACT_LOCKED_IN_MEDIA` |
| "Who founded / owns <brand>?" | a canonical fact sheet | `G3.CORROB.NO_CITABLE_PRIMARY_SOURCE` |

## `G3.ANSW.NO_QUESTION_SHAPED_HEADINGS`

### What counts as question-shaped
A heading that ends with "?", or begins with an interrogative: what, why, how, when, where, who,
which, can, could, does, do, is, are, should, will, would, may, has, have.

### Fires when
Across at least 3 sampled content pages that carry 2+ headings each, there is **no**
question-shaped heading at level 2 or below, **and** no `FAQPage` or `QAPage` markup anywhere on
the site.

### Profile scoping (a false-positive control)
Not applicable — skipped with a recorded reason — for:

| Profile | Why |
| :--- | :--- |
| ecommerce | a product detail page answers through its specification table and price, not through question headings |
| marketing_brochure | a small brochure site is not expected to carry question-led content |
| ambiguous | the site type is not established, so no content-shape expectation is applied |

### Fix
Collect the ten most common sales and support questions, using the site's own navigation labels
and product names as vocabulary. Phrase each as an h2 or h3 on the page where it belongs, answer
it in the first sentence, and where several accumulate, generate `FAQPage` markup from the visible
questions and answers (never from hidden text — see `G2.SD.FAQ_MARKUP_MISMATCH`).

## `G3.ANSW.ANSWER_BURIED`

### Mechanism
A retrieved chunk that contains the question and a paragraph of scene-setting, but not the
answer, is useless for that question.

### Algorithm
For each h2/h3 section whose heading is question-shaped, walk its sentences in order and find the
first **answer-like** sentence: one that is not itself a question, has at least 5 words, and does
not open with preamble. Preamble openers include "Great question", "Before we …", "Let's …",
"In this section/article/guide", "To answer this", "It depends", "There are many/several …",
"You may be wondering", "A common question", "Well," and "So,".

A section's answer is **buried** when that first answer-like sentence starts more than 300
characters into the section (`answerability.answer_proximity_chars`), or when no answer-like
sentence exists in a section longer than 300 characters.

### Fires when
A page has at least 2 question-shaped sections and at least half of them are buried. Results are
collapsed per template.

### Why this design and not keyword overlap
An answer does not have to repeat the question's words: "How much does it cost?" is answered by
"Plans start at £9 per month." Matching question words to answer words would flag correct pages.
Detecting *preamble* instead flags exactly the pattern that separates a question from its answer.

### Fix
Put the direct answer in the first sentence under the heading, then elaborate; move caveats after
the answer; delete scene-setting openers.

**Verify:** read only the first sentence under each question heading. It should answer the
question on its own.
