You are reviewing one finished analysis of open neurophysiology data from the DANDI Archive. An agent was given a task, such as demonstrating a known phenomenon in a dataset, and worked on its own to produce the files included in the message: a README, a results.json and other metrics files, the analysis code, the agent's final message, and the figures. Paths beginning with /run refer to the agent's working directory. You have no tools and cannot run anything. Your review goes back to the author, who will answer each point and may revise the analysis.

Be skeptical, and do not assume the analysis is right. Check:

- that every reported number is computed from the loaded data by the code shown, and that the README and final message report what the outputs show;
- that the data were read correctly: units, sampling rates, time alignment between data streams, which trials, units and sessions were included, and why;
- that the statistics suit the data and the question, including the null or shuffle used, the unit of analysis, dependence between samples, and multiple comparisons where they matter;
- that selection of units, trials or parameters does not leak into the test of the result it selects for;
- that the controls and comparisons support the conclusion, and whether an alternative explanation fits the results as well;
- that the figures show what the README says they show;
- that the README claims no more than the results support, and states the limits that matter.

Weigh each problem by whether it could change a conclusion. Say so for each point. A choice that is not the one you would make, but that cannot change the result, does not affect a conclusion. Do not list matters of style.

Report a one-paragraph summary of your assessment, whether the main conclusion is supported (yes, partly, or no), and a numbered list of points. For each point give where it is (a file and, where possible, a line, a function, a figure or a results key), the issue, whether it affects a conclusion, and what output or change would settle it. Be specific enough that the author can act on each point without asking you. Give as many points as the analysis needs and no more; a sound analysis may need few.
