# Demo commands (copy and paste one block at a time)

## Before the demo
ollama ps
(should list only qwen3.5:4b, 100% GPU; if not: ollama stop <model>)

cd C:\Users\Deepu\commit-surgeon
.venv\Scripts\Activate.ps1
python scripts\make_messy_repo.py --committed

## 1. Show the mess (one commit, "fixed stuff")
git -C sandbox_messy log --oneline
git -C sandbox_messy diff --stat main..messy-changes

## 2. Prove offline (turn Wi-Fi off first)
ping 8.8.8.8 -n 2
curl.exe http://localhost:11434/api/tags

## 3. Run the tool (answer y at the prompts, use e once to edit a subject)
python -m surgeon --repo sandbox_messy --split-branch main

## 4. Show the result
git -C sandbox_messy log --oneline
git -C sandbox_messy status --short

## 5. Safety (optional)
python -m surgeon undo --repo sandbox_messy
git -C sandbox_messy log --oneline

## Reset for another take
python scripts\make_messy_repo.py --committed
