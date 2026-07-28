# Renders the live agentic-RCA demo (Groq). Regenerate with: vhs assets/demo.tape
Output assets/demo-live-rca.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1400
Set Height 380
Set Theme "Dracula"
Set Framerate 12
Set Padding 20

# Setup (hidden): enter the project venv and clear the screen.
Hide
Type "cd /Users/vishalbanwari/Documents/GitHub/grid-copilot && source .venv/bin/activate && clear"
Enter
Sleep 800ms
Show

Sleep 500ms
# --brief now defaults to the live groq provider; events stream as the model responds.
Type "python -m grid_copilot.cli --brief"
Sleep 700ms
Enter
Sleep 26s
