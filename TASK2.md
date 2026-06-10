Ive finished TASK.md, that use a "book (../med-agent-internists)" and benchmark (Medbench-agent) to verify ollama models' correctness etc. 

Now, with this eval-tool, I can select the "right model for the right task", and I can integrate them as a system using ollama but NOT relying on APIs anymore.

For above my extended goal, how shall I upgrade this eval-tool / this repo? Example: the "book (../med-agent-internists)" maybe old-knowledge, and I may consider /autoresearch + websearch as compensation solution. NOTE: maybe eval-tool shall be dynamic sometimes, running ../med-agent-internists for specific-disease QnA -- compare with ../med-agent-internists output etc 

NOTE: To be clear, verifier/judge should be DeepSeek -- but once finished eval on ollama LLMs, I will have leadboard on them, and I can use this info to build my offline llms (MOA) solution later-on
