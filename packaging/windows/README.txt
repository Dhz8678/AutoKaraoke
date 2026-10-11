AutoKaraoke for Windows

1. Unzip this folder anywhere (e.g. in Documents).
2. Double-click AutoKaraoke.bat.
   The first start downloads Python and the editor (about 300 MB) in a console window;
   later starts open the app directly.
3. The first time you use Separate or Auto-sync, the app offers to download the AI
   components (3-4 GB with an NVIDIA card, under 1 GB without) and then the models (about 5 GB).

Projects are saved in Documents\AutoKaraoke. Downloaded components live in
%LOCALAPPDATA%\AutoKaraoke (delete that folder to uninstall them) and in the uv and
Hugging Face caches (%LOCALAPPDATA%\uv, %USERPROFILE%\.cache).

Problems? Open an issue on GitHub; the log is in %APPDATA%\autokaraoke\autokaraoke.log
