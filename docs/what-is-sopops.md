# Understand how sopops improves an agent

Use sopops to connect an agent's instructions to the calls it handles. You can
find where the agent missed a procedure, review the evidence, change the
instructions, and measure the next release.

## A release connects instructions to calls

Store shared instructions and procedures as reusable blocks. An agent combines
those blocks with its own text into a prompt. When you publish, sopops creates a
release that records the exact prompt each agent receives. [How agent
instructions are built](instructions-model.md) explains each piece and its fields.

At the start of a call, an agent fetches its instructions and records the
release it ran, or its platform gets them by webhook. With push delivery,
publishing writes the instructions into the agent on its platform. If you
change an instruction later, the next call fetches the new release. A call
that already started keeps its original release.

## A checked call links a rule to evidence

Send each finished call to sopops, by webhook or SDK. It records the
transcript, tool calls, and release, then checks the procedures that applied.
A result includes the rule, status, and quotes that support it.

Findings from similar calls become patterns. A pattern can show a real mistake,
a valid exception, an outdated procedure, or a checker mistake.

![A report groups checked calls by procedure and shows the sessions that
followed, partly followed, or deviated from each one.](images/walkthrough-report.png)

## A reviewed pattern can change the next release

Review a pattern before you change instructions. Mark it with a verdict, then
save a suggested edit as a draft or a pull request. Publishing that change
creates another release.

Compare releases in Insights to see whether the new instructions helped. Rules
that a suggestion rewords keep their earlier history, so one comparison can
cover both wordings.

![A release comparison shows release 1 and release 2 beside pass-rate and
procedure-by-procedure results.](images/walkthrough-release-compare.png)

To follow this loop with known calls and expected results, [run the local
demo](first-agent.md).
