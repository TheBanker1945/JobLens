"""Search with the advert this person would answer, not with their CV.

A CV and a vacancy are written by different people for different reasons. A CV
says "ik bouwde dashboards in Power BI"; the advert that wants that person says
"wij zoeken een data-analist met ervaring in Power BI". Embeddings close some of
that gap but not all of it, so one of the five candidates in 3.5 is: have a model
write the vacancy this CV is the answer to, and search with that instead. The
technique is called HyDE (hypothetical document embeddings) -- the invented
document never has to be true, it only has to sit in the right part of the space.

Two rules make it safe to use here:

- It is built from the **extracted profile**, not from the CV text, so the model
  gets ten fields rather than someone's life story.
- It is a **query and never evidence**. Nothing it says is shown to the user or
  quoted in a match explanation; the citations in milestone 3.6 come from the CV
  and the real vacancy. An invented advert is exactly the kind of text that would
  produce a confident, false claim if it were allowed to leak into the output.
"""

from joblens.cv.documents import profile_summary
from joblens.cv.schema import CVProfile
from joblens.llm.types import ChatClient

MAX_OUTPUT_TOKENS = 400

SYSTEM_PROMPT = """\
You write a short Dutch job advert: the vacancy that the person described below
would most plausibly be hired for next.

Rules:
- Use only what the profile states. Every skill, tool, certificate and level in
  your advert must appear in the profile. Invent nothing.
- Write it as a Dutch vacancy: a job title on the first line, then two or three
  sentences about the work, then the requirements as short lines.
- No company name, no salary, no benefits, no address: none of that is in the
  profile and none of it identifies the job.
- One step forward is allowed, not five: the next job, not a dream job.
- At most 120 words. Plain text, no markdown.
"""


def write_ideal_vacancy(profile: CVProfile, client: ChatClient) -> str:
    """The advert to search with. One call, about 0.1 cent."""
    result = client.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": profile_summary(profile)},
        ],
        temperature=0.0,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return result.content.strip()
