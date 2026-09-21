# Phase 2 — Matching my CV against the real vacancies

This is the thing JobLens was built for. I give it my CV, and it tells me which of
the real vacancies it has collected are worth my time, why each one fits, and what
I am missing.

This brief says **what** I want and how I will judge it. It deliberately does not
say how to build it. Work that out, explore what is already here first, and come
back with a plan before writing code.

## Where we are

Phase 1 made search measurable on real data, and it is worth knowing what that
cost us, because the same trap is waiting here.

Everything we believed about search turned out to be true only of the ten
made-up vacancies we tested on. On the real ones, the best option became the
worst. Two useless pages out of two hundred were quietly wrecking the results. A
conclusion about one setting turned out to be an artefact of those two pages.
None of that was visible until we measured on real data with real labels.

So the lesson I want carried into phase 2: **a demo that looks right is not
evidence.** I will believe a number over an impression, and I will believe a
number on real data over a number on invented data.

What is settled and should not be re-opened without a reason: which embedding
model we use, and which form of a vacancy we feed it. Phase 1 decided both with
numbers, and the reasoning is in the learning log.

## What I want to be able to do

Point the app at my CV. Get back a ranked list of real vacancies. For each one:

- **How well it fits**, as something I can compare between vacancies.
- **Why** — the specific things in my CV that caused the match. Not "you have
  relevant experience", but the actual lines it used.
- **What I am missing** — what the vacancy asks for that my CV does not show.

Then, across the whole list, the thing I most want: **what keeps coming up that I
do not have.** If everything I am close to needs one skill I lack, that is the
single most useful sentence this app can say to me, and it should say it.

## What a good answer looks like

A good answer is one I can check. If it says I match, I want to see what it read.
If it says I am missing something, I want to see where the vacancy asks for it.
Every claim should be traceable to a piece of text in my CV or in the vacancy.

A bad answer is a number with no reasoning, a summary that could describe anyone,
or a compliment. Worst of all is experience I do not have: if it credits me with
something my CV does not say, the whole thing is worthless, because I will stop
trusting the parts that are right. Being wrong is survivable. Being confidently
wrong is not.

It should also be willing to tell me a vacancy is a bad fit, and why. A list where
everything scores well tells me nothing.

## What I care about

**Honesty over flattery.** This is a tool for deciding where to spend my
applications. It is more useful when it disappoints me.

**My CV is not one thing.** It is several jobs, an education, and a pile of
skills. Treating it as one lump is probably the main thing standing between a
mediocre result and a good one. Phase 1 left behind some machinery for splitting
documents up that was measured and then not used, because vacancies did not need
it. A CV might. Find out — do not assume either way.

**Only send what the task needs.** A CV has my phone number, my email and my
address on it. None of that helps decide whether I fit a job. Strip it before
anything leaves this machine. Cloud models are allowed now, but allowed is not the
same as send everything.

**Anyone should be able to run this.** My real CV will never be in this repo. A
made-up one has to ship with it, so someone who clones the project can try the
whole thing end to end and get sensible output.

## How I will know it worked

- I can point it at my own CV and get back ranked vacancies, each with the
  evidence behind it and an honest list of gaps.
- The made-up CV that ships with the repo produces visibly different results —
  different jobs, different gaps. If both CVs get similar answers, it is not
  really reading them.
- There is some measurement of quality beyond my opinion. At minimum a small set
  of CV-and-vacancy pairs judged once, so a later change can be shown to help or
  hurt. Phase 1's rule stands: whoever does the judging, the file says so.
- I can see roughly what a run costs, in money and in time.

## Rules

- My CV and the real vacancies stay out of git. Anything committed as an example
  is made up.
- No claim that something is better without a number next to it.
- Any choice about what goes to the cloud stays visible and switchable, with the
  default written down.
- New dependencies are fine when they genuinely help — tell me why. Reading a PDF
  is a fair candidate.
- This is a public portfolio project. The code gets read. Write it accordingly.

## Questions I want answered before building

1. Is a made-up CV good enough to keep the repo runnable for other people, or
   does the gap between it and my real CV distort what we measure?
2. Explaining a match properly costs a model call. Does that happen for every
   vacancy, or only the best few? What does each choice cost me per CV, and what
   do I lose by picking the cheap one?
3. How do I compare two scores that came from different CVs, or from a corpus
   that has changed since? Or can I not, and should the app stop pretending I can?
4. What should it do when my CV genuinely does not fit anything it has? Silence,
   a bad list, or saying so?

## Not in this milestone

Agents and tool loops, the MCP server, the web UI, deployment. Fine-tuning is not
on the table at this scale — better prompts and better retrieval come first.
