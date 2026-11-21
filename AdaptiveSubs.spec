# PyInstaller spec for the Windows program. Build with:  .venv/Scripts/python build_app.py
# One folder (AdaptiveSubs.exe + _internal/): starts faster and trips antivirus less than one-file.
import os
import spacy
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = [
    ("player.html", "."),
    ("setup.html", "."),
    ("profile.html", "."),
    ("app.html", "."),                     # the program window: control panel
    ("assets", "assets"),
    ("data/vocab_test.json", "data"),
    ("data/exam_words.json", "data"),       # exam word lists + short meanings (ECDICT, MIT License)
    ("data/ranks_en.txt", "data"),          # English word ranks (replaces wordfreq's 58 MB of all languages)
    ("models/faster-whisper-base.en", "models/faster-whisper-base.en"),   # local speech model (lines the cloud ASR refuses)
]
datas += collect_data_files("en_core_web_sm")
datas += copy_metadata("en_core_web_sm") + copy_metadata("spacy") + copy_metadata("thinc")

# spaCy ships ~70 languages as sub-packages of spacy.lang; only English is used.
# (Shared modules directly in spacy/lang, e.g. punctuation.py, are kept.)
LANG_DIR = os.path.join(os.path.dirname(spacy.__file__), "lang")
# loading en_core_web_sm also imports these (spaCy registers their tokenizers): keep them
NEEDED_LANGS = {"en", "xx", "ja", "ko", "th", "vi", "zh", "__pycache__"}
OTHER_LANGS = {d for d in os.listdir(LANG_DIR) if os.path.isdir(os.path.join(LANG_DIR, d)) and d not in NEEDED_LANGS}
SOURCE_EXT = (".cpp", ".c", ".h", ".pyx", ".pxd")      # Cython/C++ sources: not needed to run


def keep_data(entry):
    path = entry[0].replace("\\", "/")
    parts = path.split("/")
    if "spacy" in parts and "lang" in parts:
        i = parts.index("lang")
        if i + 1 < len(parts) - 1 and parts[i + 1] in OTHER_LANGS:
            return False
    top = parts[0]
    if top in ("spacy", "thinc", "blis", "srsly", "cymem", "preshed", "murmurhash") and path.endswith(SOURCE_EXT):
        return False
    return True


def keep_module(name):
    bits = name.split(".")
    return not (len(bits) >= 3 and bits[0] == "spacy" and bits[1] == "lang" and bits[2] in OTHER_LANGS)


a = Analysis(
    ["app.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["en_core_web_sm", "spacy.lang.en", "webview", "webview.platforms.edgechromium", "webview.platforms.winforms",
                   "faster_whisper", "ctranslate2"],
    excludes=["wordfreq", "onnxruntime", "PIL", "hf_xet", "tkinter", "matplotlib", "pandas", "IPython", "pytest", "PyInstaller"],
    noarchive=False,
)
a.datas = [d for d in a.datas if keep_data(d)]
a.pure = [m for m in a.pure if keep_module(m[0])]
a.binaries = [b for b in a.binaries if keep_module(b[0].replace("\\", ".").replace("/", ".").split(".cp3")[0])]
pyz = PYZ(a.pure)
# windowed program: no console (logs go to %LOCALAPPDATA%\AdaptiveSubs\logs), icon = assets/icon.ico
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AdaptiveSubs", console=False, icon="assets/icon.ico", upx=False)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="AdaptiveSubs")
