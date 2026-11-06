"""Interface gráfica: recebe os dados, valida os campos e mostra o progresso.

A tela segue a ordem do trabalho: 1) música, 2) BPM e letra com cifras,
3) ajustes opcionais (agrupados em abas), 4) gerar. O bloco "if start" inicia a conversão."""

from __future__ import annotations

import json
import os
import re
import time
import shutil
import sys
from importlib.metadata import version, PackageNotFoundError
import uuid
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from pipeline import (
    run_pipeline,
    revise_lyrics,
    classify_stem_name,
)

from saved_results import list_saved_jobs, recover_result, audio_preview
from song_names import song_title, rename_song_result
from midi_mix import DEFAULT_MIX, validate_mix
from job_paths import find_moved_job
from pipeline import remix_result
from lyrics import read_timed_lyrics
from music_guide import validate_required_inputs
from lyrics_only import lyric_lines, insert_lyrics, align_and_insert_lyrics
from quality import fixed_settings
from output_settings import load_destination, save_destination, save_lyric_files, directory_choices, directory_roots, accessible_directory, resource_directory, configure_resources


def mix_controls(prefix, settings=None, allow_mute=True):
    """Monta os controles de volume e retorna os valores escolhidos pelo usuário."""
    values = validate_mix(settings)
    result = {}
    left, right = st.columns(2)
    sliders = [
        ("bass", "Baixo"),
        ("drums", "Bateria geral"),
        ("kick", "Bumbo"),
        ("snare", "Caixa"),
        ("toms", "Tons / surdo"),
        ("hihat", "Chimbal"),
        ("cymbals", "Pratos"),
        ("piano_solo", "Piano Solo"),
        ("piano", "Piano base"),
    ]
    for index, (name, label) in enumerate(sliders):
        with (left, right)[index % 2]:
            result[name] = st.slider(
                label + " (%)", 0, 150, int(values.get(name, 100)), 5, key=prefix + name
            )
    with (left, right)[len(sliders) % 2]:
        result["other"] = st.slider(
            "Segundo piano / Acompanhamento (%)",
            0 if allow_mute else 5,
            150,
            max(int(values.get("other", 70)), 0 if allow_mute else 5),
            5,
            key=prefix + "other",
            help="Faixa 'Outros' (segundo piano / teclado base). 0% silencia; 60-75% é o fundo ideal.",
        )
    result["lower_second_piano"] = st.checkbox(
        "Abaixar segundo piano automaticamente (fundo suave)",
        value=bool(settings.get("lower_second_piano", True)) if settings else True,
        key=prefix + "lower_second_piano",
        help="Mantém o segundo piano em volume mais baixo para dar destaque à melodia e ao piano 1.",
    )
    result["punch"] = st.slider(
        "Pressão do baixo e do bumbo (%)",
        0,
        100,
        int(values["punch"]),
        5,
        key=prefix + "punch",
        help="Reforça as notas fracas e uniformiza a batida. 0% mantém as intensidades originais.",
    )
    programs = {"Piano": 0, "Piano elétrico": 4, "Órgão": 16, "Sanfona": 21}
    label = st.selectbox(
        "Timbre do teclado",
        list(programs),
        index=list(programs.values()).index(values["piano_program"]),
        key=prefix + "piano_program",
    )
    result["piano_program"] = programs[label]
    st.caption("100% mantém o nível original. O som final também depende do seu sintetizador.")
    return result


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("MIDI_STUDIO_WORK_DIR", str(BASE_DIR / "trabalhos")))
WORK_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WORK_DIR = WORK_DIR
SETTINGS_FILE = DEFAULT_WORK_DIR.parent / "preferencias_saida.json"

# 1. Aparência. Sem barra lateral: tudo na ordem em que é usado.
st.set_page_config(
    page_title="MidiDream",
    page_icon="🎼",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Estilo inspirado no Material Design do Google — paleta azul Google, tipografia Roboto.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&family=Google+Sans:wght@400;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Roboto', 'Google Sans', sans-serif;
    }

    .block-container {
        max-width: 880px;
        padding-top: 1.5rem;
        padding-bottom: 3rem;
    }

    [data-testid="stSidebar"],
    [data-testid="collapsedControl"] { display: none; }

    /* ── Hero ─────────────────────────────────────────── */
    .md-hero {
        background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%);
        border-radius: 16px;
        padding: 2rem 2.2rem 1.8rem;
        margin-bottom: 1.8rem;
        color: #fff;
        box-shadow: 0 4px 18px rgba(26,115,232,.35);
    }
    .md-hero h1 {
        margin: 0 0 .3rem 0;
        font-size: 2.1rem;
        font-weight: 700;
        letter-spacing: -.5px;
        color: #fff;
    }
    .md-hero p {
        margin: 0;
        font-size: .95rem;
        opacity: .88;
        color: #e8f0fe;
        font-weight: 400;
    }
    .md-hero .badge {
        display: inline-block;
        background: rgba(255,255,255,.18);
        border: 1px solid rgba(255,255,255,.30);
        border-radius: 20px;
        padding: .15rem .65rem;
        font-size: .78rem;
        margin-top: .6rem;
        font-weight: 500;
    }

    /* ── Cards de seção ───────────────────────────────── */
    .md-section-label {
        display: flex;
        align-items: center;
        gap: .5rem;
        font-size: .72rem;
        font-weight: 700;
        letter-spacing: 1.2px;
        text-transform: uppercase;
        color: #1a73e8;
        margin: 1.8rem 0 .5rem 0;
    }
    .md-section-label::after {
        content: "";
        flex: 1;
        height: 1.5px;
        background: linear-gradient(90deg, #1a73e8 0%, transparent 100%);
        opacity: .25;
        margin-left: .4rem;
    }

    /* ── Métricas ──────────────────────────────────────── */
    div[data-testid="stMetric"] {
        background: #f8f9ff;
        border: 1.5px solid #e8eaed;
        padding: .8rem 1rem;
        border-radius: 12px;
        transition: box-shadow .15s;
    }
    div[data-testid="stMetric"]:hover {
        box-shadow: 0 2px 10px rgba(26,115,232,.12);
        border-color: #aecbfa;
    }
    div[data-testid="stMetric"] > label {
        font-size: .78rem !important;
        color: #5f6368 !important;
        font-weight: 500 !important;
    }

    /* ── Botão principal ──────────────────────────────── */
    div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #1a73e8, #1557b0);
        color: #fff;
        border: none;
        border-radius: 24px;
        min-height: 3.2rem;
        font-weight: 700;
        font-size: 1rem;
        letter-spacing: .3px;
        box-shadow: 0 2px 8px rgba(26,115,232,.4);
        transition: all .18s ease;
    }
    div.stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #1557b0, #0d47a1);
        box-shadow: 0 4px 16px rgba(26,115,232,.55);
        transform: translateY(-1px);
    }
    div.stButton > button[kind="primary"]:active {
        transform: translateY(0);
        box-shadow: 0 1px 4px rgba(26,115,232,.3);
    }

    /* ── Botões secundários ───────────────────────────── */
    div.stButton > button:not([kind="primary"]) {
        border-radius: 20px;
        border: 1.5px solid #dadce0;
        color: #1a73e8;
        font-weight: 500;
        transition: all .15s;
    }
    div.stButton > button:not([kind="primary"]):hover {
        background: #e8f0fe;
        border-color: #aecbfa;
    }

    /* ── Download buttons ─────────────────────────────── */
    div.stDownloadButton > button {
        border-radius: 20px;
        border: 1.5px solid #dadce0;
        transition: all .15s;
    }
    div.stDownloadButton > button[kind="primary"] {
        background: linear-gradient(135deg, #1a73e8, #1557b0);
        color: #fff;
        border: none;
        border-radius: 24px;
        font-weight: 700;
        box-shadow: 0 2px 8px rgba(26,115,232,.4);
    }

    /* ── Expanders ────────────────────────────────────── */
    div[data-testid="stExpander"] {
        border: 1.5px solid #e8eaed;
        border-radius: 12px;
        overflow: hidden;
    }
    div[data-testid="stExpander"] > details > summary {
        padding: .8rem 1rem;
        font-weight: 500;
        color: #202124;
    }

    /* ── Inputs e Selectbox ───────────────────────────── */
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stTextArea"] textarea {
        border-radius: 8px;
        border: 1.5px solid #dadce0;
        transition: border-color .15s;
    }
    div[data-testid="stTextInput"] input:focus,
    div[data-testid="stNumberInput"] input:focus,
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #1a73e8;
        box-shadow: 0 0 0 3px rgba(26,115,232,.12);
    }

    /* ── Status box de progresso ─────────────────────── */
    div[data-testid="stStatus"] {
        border-radius: 12px;
        border: 1.5px solid #e8eaed;
    }

    /* ── File uploader ────────────────────────────────── */
    div[data-testid="stFileUploader"] {
        border-radius: 12px;
    }

    /* ── Tabs ─────────────────────────────────────────── */
    div[data-testid="stTabs"] > div > div > button[data-selected="true"] {
        color: #1a73e8 !important;
        border-bottom: 2px solid #1a73e8 !important;
        font-weight: 500;
    }

    /* ── Cabeçalhos de seção ──────────────────────────── */
    h2, h3 {
        margin-top: 1.6rem;
        font-weight: 500;
        color: #202124;
    }

    /* ── Chips de informação ──────────────────────────── */
    .md-chip {
        display: inline-flex;
        align-items: center;
        gap: .3rem;
        background: #e8f0fe;
        color: #1a73e8;
        border-radius: 20px;
        padding: .2rem .75rem;
        font-size: .82rem;
        font-weight: 500;
        margin: .15rem;
    }
    </style>

    <div class="md-hero">
      <h1>🎼 MidiDream</h1>
      <p>Transforme uma música em MIDI e karaokê (.kar) com separação de instrumentos por IA</p>
      <span class="badge">versão 4.4</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="md-section-label">📁 Destino dos arquivos</div>', unsafe_allow_html=True)
st.subheader("Onde salvar")
if "destination_path" not in st.session_state:
    st.session_state["destination_path"] = load_destination(SETTINGS_FILE, DEFAULT_WORK_DIR)
def open_folder_browser(target):
    st.session_state["folder_browser_target"] = target
    st.session_state["folder_browser_path"] = st.session_state[target]


def browse_into(path):
    st.session_state["folder_browser_path"] = str(path)


def use_browsed_folder():
    st.session_state[st.session_state["folder_browser_target"]] = st.session_state["folder_browser_path"]
    st.session_state.pop("folder_browser_target", None)


if "resources_path" not in st.session_state:
    default_models = Path(os.environ.get("HF_HOME", str(DEFAULT_WORK_DIR.parent / "modelos" / "huggingface"))).parent
    st.session_state["resources_path"] = resource_directory(SETTINGS_FILE, default_models)
st.button("📁 Escolher pasta", on_click=open_folder_browser, args=("destination_path",))
if st.session_state.get("folder_browser_target"):
    with st.container(border=True):
        st.write("Escolha a pasta")
        roots = directory_roots()
        if roots:
            root = st.selectbox("Disco / local", roots, format_func=str, key="browser_root")
            st.button("Abrir disco / local", on_click=browse_into, args=(root,))
        else:
            st.warning("Nenhuma unidade acessível. Digite o caminho no campo Pasta de destino.")
        folder = Path(st.session_state["folder_browser_path"]).expanduser()
        if not accessible_directory(folder):
            folder = Path.home()
            st.session_state["folder_browser_path"] = str(folder)
        st.code(str(folder), language=None)
        st.button("⬆ Pasta anterior", on_click=browse_into, args=(folder.parent,))
        try:
            children = directory_choices(folder)
            if children:
                child = st.selectbox("Subpastas", children, format_func=lambda p: p.name,
                                     key="browser_child_" + str(folder))
                st.button("Abrir subpasta", on_click=browse_into, args=(child,))
            else:
                st.caption("Esta pasta não tem subpastas.")
        except OSError as exc:
            st.warning("Não foi possível listar as subpastas: " + str(exc))
        st.button("✅ Usar esta pasta", on_click=use_browsed_folder, type="primary")
destination_text = st.text_input("Pasta de destino", key="destination_path")
try:
    WORK_DIR = save_destination(SETTINGS_FILE, destination_text)
except (OSError, ValueError) as exc:
    st.error("Pasta inválida ou sem permissão de gravação: " + str(exc))
    st.stop()
st.caption("A pasta fica salva para os próximos usos. Cada música ganha uma subpasta própria.")

with st.expander("Pasta dos modelos e cache"):
    st.caption("Os modelos ocupam vários GB. Escolha um disco com espaço livre. Arquivos já baixados não são movidos automaticamente.")
    st.button("📁 Escolher pasta dos modelos", on_click=open_folder_browser, args=("resources_path",))
    if st.button("Usar o destino da música para modelos e cache"):
        st.session_state["resources_path"] = str(WORK_DIR / "_recursos_midi_studio")
    resource_text = st.text_input("Modelos e cache", key="resources_path")
try:
    RESOURCE_DIR = configure_resources(SETTINGS_FILE, resource_text)
except (OSError, ValueError) as exc:
    st.error("Pasta dos modelos inválida: " + str(exc))
    st.stop()
free_work = shutil.disk_usage(WORK_DIR).free / 1024**3
free_models = shutil.disk_usage(RESOURCE_DIR).free / 1024**3
st.caption(f"Espaço livre: saída {free_work:.1f} GB · disco dos modelos {free_models:.1f} GB")

operation = st.radio("O que deseja fazer?", ["Converter música", "Colocar somente letra em MIDI"], horizontal=True)
if operation == "Colocar somente letra em MIDI":
    st.caption("Envie o MIDI e cole a letra. Adicione o MP3 para sincronizar automaticamente com o canto. Não precisa de BPM ou cifras.")
    original = st.file_uploader("MIDI original", type=["mid", "midi", "kar"], key="lyrics_only_midi")
    text = st.text_area("Letra", height=220, key="lyrics_only_text")
    text_file = st.file_uploader("Ou importe a letra em TXT", type=["txt"], key="lyrics_only_txt")
    if text_file is not None and not text.strip():
        try:
            text = text_file.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            text = text_file.getvalue().decode("cp1252")
    audio = st.file_uploader("MP3 da música para sincronizar a letra (opcional)",
                             type=["mp3", "wav", "flac", "m4a", "ogg"], key="lyrics_only_audio")
    automatic = audio is not None and st.checkbox("Sincronizar automaticamente pelo canto", value=True)
    if automatic:
        language_label = st.selectbox("Idioma da letra", ["Português", "English", "Español"], key="lyrics_only_language")
        alignment_language = {"Português": "pt", "English": "en", "Español": "es"}[language_label]
        alignment_offset = st.number_input("Ajuste entre MP3 e MIDI (segundos)", value=0.0, step=0.1,
                                          help="0 se começam juntos. Positivo atrasa a letra; negativo adianta.", key="lyrics_only_offset")
        st.caption("Use MP3 e MIDI da mesma versão da música. O primeiro uso baixa o modelo de voz; os tempos são estimados pelo canto.")
    if original is not None:
        import io
        import mido
        try:
            source_midi = mido.MidiFile(file=io.BytesIO(original.getvalue()), charset="latin1")
            if source_midi.type == 2:
                raise ValueError("Use um MIDI tipo 0 ou 1.")
            duration = float(source_midi.length)
            if duration <= 0:
                raise ValueError("Este MIDI não tem duração válida.")
            st.caption(f"Duração: {duration:.2f} segundos. Instrumentos e andamento serão preservados.")
            token = hashlib.sha256(original.getvalue()).hexdigest()[:12]
            if automatic:
                signature = hashlib.sha256(original.getvalue() + audio.getvalue() +
                    (text + alignment_language + str(alignment_offset)).encode()).hexdigest()
                if st.button("Sincronizar letra pelo MP3", type="primary", key="auto_lyrics_only", disabled=not text.strip()):
                    progress_bar = st.progress(0.0, text="Localizando a letra no canto…")
                    with tempfile.TemporaryDirectory(prefix="letra_mp3_") as folder:
                        source = Path(folder) / "original.mid"
                        output = Path(folder) / "com_letra.mid"
                        audio_path = Path(folder) / ("referencia" + Path(audio.name).suffix.lower())
                        source.write_bytes(original.getvalue())
                        audio_path.write_bytes(audio.getvalue())
                        report = align_and_insert_lyrics(source, output, audio_path, text,
                            language=alignment_language, offset=alignment_offset,
                            progress=lambda fraction: progress_bar.progress(fraction, text="Sincronizando a letra com o canto…"))
                        st.session_state["lyrics_auto_output"] = (signature, output.read_bytes(), report)
                        saved = save_lyric_files(WORK_DIR, Path(original.name).stem + "_letra_sincronizada", output.read_bytes())
                        st.session_state["lyrics_saved_folder"] = str(saved)
                    progress_bar.progress(1.0, text="Sincronização concluída.")
                generated = st.session_state.get("lyrics_auto_output")
                if generated and generated[0] == signature:
                    name = Path(original.name).stem + "_letra_sincronizada"
                    st.success(f"Letra sincronizada: {generated[2]['words']} palavras localizadas. Notas e instrumentos preservados.")
                    st.download_button("Baixar MIDI com letra", generated[1], name + ".mid", mime="audio/midi")
                    st.download_button("Baixar karaokê KAR", generated[1], name + ".kar", mime="audio/midi")
            else:
                start = st.number_input("Início do canto (segundos)", min_value=0.0, max_value=duration,
                                        value=0.0, step=0.1, key="lyric_start_" + token)
                end = st.number_input("Fim do canto (segundos)", min_value=0.0, max_value=duration,
                                      value=duration, step=0.1, key="lyric_end_" + token)
                if text.strip() and end > start:
                    rows = lyric_lines(text, start, end)
                    st.info("Os tempos iniciais são aproximados. Ajuste as linhas abaixo para acompanhar o canto.")
                    signature = hashlib.sha256((token + text + str(start) + str(end)).encode()).hexdigest()[:16]
                    edited = st.data_editor(rows, hide_index=True, num_rows="dynamic", width="stretch",
                        key="lyric_rows_" + signature, column_config={
                            "inicio": st.column_config.NumberColumn("Início (s)", format="%.2f"),
                            "fim": st.column_config.NumberColumn("Fim (s)", format="%.2f"),
                            "texto": st.column_config.TextColumn("Letra"),
                        })
                    request = hashlib.sha256((signature + json.dumps(edited, ensure_ascii=False)).encode()).hexdigest()
                    if st.button("Colocar letra", type="primary", key="insert_lyrics_only"):
                        with tempfile.TemporaryDirectory(prefix="somente_letra_") as folder:
                            source, output = Path(folder) / "original.mid", Path(folder) / "com_letra.mid"
                            source.write_bytes(original.getvalue())
                            insert_lyrics(source, output, edited)
                            st.session_state["lyrics_only_output"] = (request, output.read_bytes())
                            saved = save_lyric_files(WORK_DIR, Path(original.name).stem + "_com_letra", output.read_bytes())
                            st.session_state["lyrics_saved_folder"] = str(saved)
                    generated = st.session_state.get("lyrics_only_output")
                    if generated and generated[0] == request:
                        name = Path(original.name).stem + "_com_letra"
                        st.success("Letra adicionada. As notas e os instrumentos foram preservados.")
                        st.download_button("Baixar MIDI com letra", generated[1], name + ".mid", mime="audio/midi")
                        st.download_button("Baixar karaokê KAR", generated[1], name + ".kar", mime="audio/midi")
        except Exception as exc:
            st.error(str(exc))
    if st.session_state.get("lyrics_saved_folder"):
        st.info("Arquivos salvos em: " + st.session_state["lyrics_saved_folder"])
    st.stop()

# 2. Conversões anteriores: só aparece se houver algo salvo.
saved_jobs = list_saved_jobs(WORK_DIR)
if saved_jobs:
    with st.expander("📂 Abrir conversão salva", expanded=not bool(st.session_state.get("last_result"))):
        selected_job = st.selectbox(
            "Conversão",
            saved_jobs,
            format_func=lambda job: (
                datetime.fromtimestamp(
                    (job / "resultados/midi/completo.mid").stat().st_mtime
                ).strftime("%d/%m %H:%M")
                + " · "
                + song_title(job)
            ),
            label_visibility="collapsed",
        )
        open_col, name_col = st.columns([1, 2])
        if open_col.button("Abrir", type="primary", width="stretch"):
            try:
                st.session_state["last_result"] = recover_result(selected_job)
                st.session_state["last_job_id"] = selected_job.name
                st.success("Arquivos recuperados sem repetir a conversão.")
            except Exception as exc:
                st.error("Não foi possível abrir essa conversão: " + str(exc))
        saved_title = name_col.text_input(
            "Renomear",
            value=song_title(selected_job),
            key="saved_name_" + selected_job.name,
            label_visibility="collapsed",
            placeholder="Novo nome (Enter para renomear)",
        )
        if saved_title.strip() and saved_title.strip() != song_title(selected_job):
            try:
                renamed = rename_song_result(recover_result(selected_job), saved_title)
                st.session_state["last_result"] = {
                    key: str(value) if isinstance(value, Path) else value
                    for key, value in renamed.items()
                }
                st.session_state["last_job_id"] = Path(renamed["job_dir"]).name
                st.rerun()
            except Exception as exc:
                st.error("Não foi possível renomear: " + str(exc))

st.markdown('<div class="md-section-label">🎵 Passo 1 — Música</div>', unsafe_allow_html=True)
# 3. Passo 1 — a música.
st.subheader("1 · Música")
source_mode = st.radio(
    "Fonte",
    ["Link do YouTube", "Arquivo MP3 / áudio", "Faixas separadas"],
    horizontal=True,
    label_visibility="collapsed",
)
uploaded_files = []
track_assignments = []
youtube_url = ""
audio_types = ["mp3", "wav", "flac", "m4a", "ogg", "aac", "opus", "webm"]

if source_mode == "Link do YouTube":
    youtube_url = st.text_input(
        "Link do vídeo no YouTube",
        placeholder="Ex: https://www.youtube.com/watch?v=a0v4ucMOfj4 ou https://youtu.be/...",
        key="youtube_url_input",
        help="Cole qualquer link do YouTube (vídeos, shorts, playlists ou rádio).",
    )
    use_browser_session = st.checkbox(
        "Usar minha sessão do navegador (se o YouTube pedir confirmação de login ou bloquear)",
        key="use_browser_session",
        help="Lê os cookies da conta logada no navegador. Importante: use uma guia NORMAL (não anônima) e feche o navegador antes de iniciar.",
    )
    selected_browser = None
    if use_browser_session:
        selected_browser = st.selectbox(
            "Selecione o navegador onde sua conta do YouTube está conectada:",
            options=["edge", "chrome", "firefox", "brave"],
            format_func=lambda x: {
                "edge": "Microsoft Edge",
                "chrome": "Google Chrome",
                "firefox": "Mozilla Firefox",
                "brave": "Brave Browser",
            }.get(x, x),
            key="selected_browser_choice",
        )
        st.caption(
            "⚠️ **Atenção:** Feche todas as janelas do navegador selecionado antes de clicar em Iniciar (o Windows bloqueia o arquivo de cookies se o navegador estiver aberto)."
        )
    st.info(
        "💡 **Dica de Ouro para Karaokê Perfeito:**\n\n"
        "Para obter o melhor resultado, **coloque o link de vídeos que já vêm com o playback / karaokê instrumental** (sem voz cantada).\n\n"
        "Ao usar uma base de karaokê e colar a letra no Passo 2, o MidiDream sincroniza a letra automaticamente no tempo exato compasso a compasso com o playback, gerando os arquivos **.mid** e **.kar** 100% afinados e sincronizados para você cantar!"
    )
elif source_mode == "Arquivo MP3 / áudio":
    music = st.file_uploader(
        "Música completa (MP3, WAV, FLAC…)",
        type=audio_types,
        accept_multiple_files=False,
        key="upload_song",
        label_visibility="collapsed",
    )
    uploaded_files = [music] if music else []
else:
    uploaded_files = st.file_uploader(
        "Selecione todas as faixas separadas de uma vez",
        type=audio_types,
        accept_multiple_files=True,
        key="upload_stems",
        help="Pode enviar só as faixas que tiver. Todas devem começar no mesmo ponto, preservando o silêncio inicial.",
    )
    groups = {
        "Voz": "vocals",
        "Baixo": "bass",
        "Bateria": "drums",
        "Piano / teclado": "piano",
        "Guitarra / violão (legado)": "guitar",
        "Sanfona": "accordion",
        "Violão": "acoustic_guitar",
        "Guitarra elétrica": "electric_guitar",
        "Outros instrumentos": "other",
    }
    labels = list(groups)
    for index, file in enumerate(uploaded_files):
        detected = classify_stem_name(file.name)
        default = next(
            (i for i, value in enumerate(groups.values()) if value == detected), len(labels) - 1
        )
        label = st.selectbox(
            "Instrumento: " + file.name,
            labels,
            index=default,
            key=f"stem_kind_{index}_{file.name}",
        )
        track_assignments.append(groups[label])

music_title = st.text_input(
    "Nome da música (opcional)",
    help="Se vazio, usa o nome do arquivo ou do vídeo do YouTube. Identifica a pasta e o histórico.",
)

st.markdown('<div class="md-section-label">🎵 Passo 2 — BPM e Letra</div>', unsafe_allow_html=True)
# 4. Passo 2 — BPM e letra com cifras (obrigatórios).
st.subheader("2 · BPM, escala e letra com cifras")
col_bpm, col_key = st.columns([1, 1])
with col_bpm:
    bpm_override = st.number_input(
        "BPM da música (opcional)",
        min_value=30.0,
        max_value=300.0,
        value=None,
        step=0.5,
        placeholder="Deixe em branco para detectar automaticamente",
        help="Opcional. Se vazio, o MidiDream detecta o BPM com alta precisão diretamente do áudio.",
    )
with col_key:
    scale_options = [
        "Detectar automaticamente pelas cifras",
        "C maior", "G maior", "D maior", "A maior", "E maior", "B maior", "F# maior",
        "F maior", "Bb maior", "Eb maior", "Ab maior", "Db maior",
        "A menor", "E menor", "B menor", "F# menor", "C# menor", "G# menor",
        "D menor", "G menor", "C menor", "F menor", "Bb menor", "Eb menor",
    ]
    key_selection = st.selectbox(
        "Escala / Tom da música",
        scale_options,
        index=0,
        help="Garante que todos os instrumentos sigam a harmonia e a escala desejada. Cifras orientam os acordes de cada momento.",
    )
    tonality_choice = None if key_selection == "Detectar automaticamente pelas cifras" else key_selection
manual_lyrics = st.text_area(
    "Letra com cifras",
    height=200,
    placeholder="G               D\nHoje eu voltei no passado\nEm7             C9\nE lembrei de nós dois…",
    help="Cifras na linha acima da letra. Aceita G, G4, G7M, Em7, C9, F#7M(9), C/G… Colchetes são opcionais; também vale [00:12.5 Am].",
)
lyrics_upload = st.file_uploader("…ou importe um .txt", type=["txt"])
if lyrics_upload is not None and not manual_lyrics.strip():
    try:
        manual_lyrics = lyrics_upload.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        manual_lyrics = lyrics_upload.getvalue().decode("cp1252")

st.markdown('<div class="md-section-label">⚙️ Passo 3 — Ajustes opcionais</div>', unsafe_allow_html=True)
# 5. Passo 3 — ajustes opcionais. A qualidade é fixa no nível máximo: não há o que escolher.
st.subheader("3 · Ajustes opcionais")
st.caption(
    "A conversão sempre usa a máxima qualidade: separação Demucs completa, conferência de timbre dos "
    "instrumentos, notas conforme o áudio original e letra alinhada ao canto."
)
with st.expander("Voz, volumes e arquivos extras", expanded=False):
    tab_voice, tab_volume, tab_more = st.tabs(["Voz e letra", "Volumes", "Mais"])

    with tab_voice:
        vocal_label = st.selectbox("Instrumento da melodia", ["Choir Aahs", "Flute"], index=1)
        vocal_program = 52 if vocal_label == "Choir Aahs" else 73
        language_label = st.selectbox(
            "Idioma da letra", ["Português", "English", "Español", "Detectar automaticamente"]
        )
        language = {
            "Português": "pt",
            "English": "en",
            "Español": "es",
            "Detectar automaticamente": "auto",
        }[language_label]

    with tab_volume:
        mix_settings = mix_controls(
            "new_mix_v27_",
            {
                "bass": 100,
                "drums": 100,
                "kick": 100,
                "snare": 100,
                "toms": 100,
                "hihat": 100,
                "cymbals": 100,
                "other": 100,
                "punch": 70,
            },
            allow_mute=True,
        )

    with tab_more:
        reference_upload = st.file_uploader(
            "MIDI/KAR de referência (mesma música)",
            type=["mid", "kar"],
            help="Orienta fase da grade, timbres e dinâmica. Não substitui BPM nem letra com cifras.",
        )
        approved_kar_upload = st.file_uploader(
            "Reaproveitar letra de um karaokê aprovado",
            type=["kar", "mid"],
            help="Preserva palavras e tempos do seu karaoke.kar e pula o reconhecimento.",
        )
        timed_upload = st.file_uploader(
            "Importar sílabas com tempos revisados (JSON)",
            type=["json"],
            help='Lista de {"seconds": 1.2, "text": "sí"}. Use [QUEBRA] para mudar de linha.',
        )
        live_mode = st.checkbox("Gravação ao vivo: reduzir notas da plateia", value=False)
        live_vocals = "off"
        if live_mode:
            live_label = st.selectbox("Filtro da voz ao vivo", ["Moderado", "Forte"])
            live_vocals = "strict" if live_label == "Forte" else "balanced"
        crowd_ranges = st.text_area(
            "Trechos só com plateia — sem melodia (um por linha)",
            placeholder="01:20-01:28\n02:10-02:14",
            height=80,
        )
        include_stems_in_zip = st.checkbox(
            "Incluir WAVs dos stems no ZIP",
            value=False,
            help="Pode gerar um ZIP muito grande.",
        )
st.markdown('<div class="md-section-label">🚀 Passo 4 — Gerar MIDI</div>', unsafe_allow_html=True)
# 6. Passo 4 — gerar. O botão só é liberado com BPM e letra com cifras válidos.
st.subheader("4 · Gerar")
render_samples = st.checkbox("Gerar MP3 com os samples incluídos (todos os instrumentos)", value=True)
st.caption("Banco GeneralUser GS. O MP3 usa estes timbres; o som do MIDI/KAR depende do player ou teclado.")
required_error = ""
try:
    validate_required_inputs(manual_lyrics, bpm_override)
except ValueError as exc:
    required_error = str(exc)
if required_error:
    st.info(required_error)
start = st.button(
    "🚀 PROCESSAR MÚSICA", type="primary", width="stretch", disabled=bool(required_error)
)

# 5. Cria o trabalho, chama a pipeline e acompanha as etapas de processamento.
if start:
    try:
        validate_required_inputs(manual_lyrics, bpm_override)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    if source_mode == "Link do YouTube":
        if not youtube_url or not youtube_url.strip():
            st.error("Cole o link de um vídeo do YouTube antes de iniciar.")
            st.stop()
    elif not uploaded_files:
        st.error("Selecione a música ou as faixas separadas antes de iniciar.")
        st.stop()

    if min(shutil.disk_usage(WORK_DIR).free, shutil.disk_usage(RESOURCE_DIR).free) < 512 * 1024**2:
        st.error("Espaço insuficiente: deixe pelo menos 512 MB livres na saída e no disco dos modelos. A primeira execução dos modelos pode exigir vários GB adicionais. Escolha outro disco acima.")
        st.stop()
    st.session_state.pop("last_result", None)
    st.session_state.pop("last_job_id", None)
    job_id = uuid.uuid4().hex[:10]
    job_dir = WORK_DIR / f"job_{job_id}"
    input_dir = job_dir / "entrada"
    input_dir.mkdir(parents=True, exist_ok=True)
    audio_path = None
    stems_dir = None

    if uploaded_files:
        saved_paths = []
        for index, uploaded in enumerate(uploaded_files):
            safe_name = (
                f"{track_assignments[index]}_{index + 1:02d}" + Path(uploaded.name).suffix.lower()
                if source_mode == "Faixas separadas"
                else Path(uploaded.name).name
            )
            destination = input_dir / safe_name
            # Evita sobrescrever se houver dois arquivos com o mesmo nome.
            if destination.exists():
                destination = input_dir / f"{len(saved_paths) + 1}_{safe_name}"
            with destination.open("wb") as output_file:
                output_file.write(uploaded.getbuffer())
            saved_paths.append(destination)
        if source_mode == "Faixas separadas":
            stems_dir = input_dir  # Inclusive quando há só uma faixa isolada.
        else:
            audio_path = saved_paths[0]

    reference_path = None
    if reference_upload is not None:
        reference_path = job_dir / "referencia_instrumental.mid"
        reference_path.write_bytes(reference_upload.getbuffer())

    stage_box = st.status("Preparando processamento…", expanded=True)
    stage_lines = []
    log_placeholder = st.empty()
    progress_bar = st.progress(0, text="Preparando…")
    started_at = time.monotonic()

    def report(stage: str, detail: str) -> None:
        line = f"[{stage.upper()}] {detail}"
        if not stage_lines or stage_lines[-1] != line:
            stage_lines.append(line)
        elapsed = int(time.monotonic() - started_at)
        match = re.search(r"(\d+(?:\.\d+)?)%", detail)
        percent = min(100, int(float(match[1]))) if match else (100 if stage == "finalizado" else 0)
        # Percentual da etapa atual; não é uma estimativa inventada do tempo restante.
        progress_bar.progress(
            percent, text=f"{detail} · {elapsed // 60}min {elapsed % 60:02d}s decorridos"
        )
        stage_box.update(label=detail, state="running", expanded=True)
        log_placeholder.code("\n".join(stage_lines[-12:]), language="text")

    try:
        timed_lyrics = (
            json.loads(timed_upload.getvalue().decode("utf-8-sig")) if timed_upload else None
        )
        if approved_kar_upload is not None:
            if timed_upload is not None:
                raise ValueError(
                    "Escolha o karaokê aprovado ou o JSON de sílabas, apenas um deles."
                )
            timed_lyrics = read_timed_lyrics(approved_kar_upload.getvalue())
        if timed_lyrics is not None and (not isinstance(timed_lyrics, list) or not timed_lyrics):
            raise ValueError("O JSON deve conter uma lista não vazia de eventos seconds/text.")
        # Se o usuário enviou um arquivo, use o primeiro como a referência da duração.
        result = run_pipeline(
            job_dir,
            require_guide=True,
            song_title=music_title.strip()
            or (
                Path(uploaded_files[0].name).stem
                if source_mode == "Arquivo MP3 / áudio" and uploaded_files
                else "Faixas separadas"
                if source_mode == "Faixas separadas"
                else "Música do YouTube"
            ),
            organize_folder=True,
            youtube_url=youtube_url.strip() if source_mode == "Link do YouTube" else "",
            youtube_browser=(
                selected_browser
                if source_mode == "Link do YouTube" and use_browser_session
                else None
            ),
            mix_settings=mix_settings,
            musical_reference=reference_path,
            render_samples=render_samples,
            audio_path=audio_path,
            uploaded_stems_dir=stems_dir,
            timed_lyrics=timed_lyrics,
            bpm_override=float(bpm_override) if bpm_override is not None and str(bpm_override).strip() else None,
            language=language,
            manual_lyrics=manual_lyrics,
            live_vocals=live_vocals,
            crowd_ranges=crowd_ranges,
            include_stems_in_zip=include_stems_in_zip,
            progress=report,
            cache_root=RESOURCE_DIR / "cache_processamento",
            **fixed_settings(
                stems_provided=source_mode == "Faixas separadas",
                vocal_program=vocal_program,
                tonality=tonality_choice,
            ),
        )
        stage_box.update(label="Processamento concluído", state="complete", expanded=False)
        st.session_state["last_result"] = {
            key: str(value) if isinstance(value, Path) else value for key, value in result.items()
        }
        st.session_state["last_job_id"] = job_id
        st.success("MIDI gerado. Confira e baixe os arquivos abaixo.")
    except Exception as exc:
        stage_box.update(label="O processamento encontrou um erro", state="error", expanded=True)
        st.error(str(exc))
        st.markdown(
            "**Dicas para resolver:** confira sua conexão, o espaço em disco e o diagnóstico na janela principal."
        )
        st.session_state["last_error"] = str(exc)

if st.session_state.get("last_result"):
    result = st.session_state["last_result"]
    current_job = find_moved_job(result["job_dir"], result.get("source_audio"))
    if current_job.is_dir() and (
        str(current_job) != str(result["job_dir"])
        or not Path(result.get("source_audio", "")).is_file()
    ):
        try:
            result = recover_result(current_job)
            st.session_state["last_result"] = result
        except (OSError, ValueError) as exc:
            st.warning(
                "Não consegui atualizar os caminhos dessa conversão. Abra a pasta correta em Recuperar conversões já salvas. "
                + str(exc)
            )
    st.divider()
    st.header(
        "Prévia (até 30 s) pronta" if result.get("preview_seconds") else "✅ Arquivos prontos"
    )
    st.caption(result.get("song_title") or song_title(Path(result["job_dir"])))
    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Duração estimada", f"{float(result.get('duration', 0)):.1f} s")
    metric2.metric("Palavras reconhecidas", str(result.get("detected_word_count", 0)))
    detection_file = Path(result["result_dir"]) / "instrumentos.json"
    if detection_file.is_file():
        with st.expander("Instrumentos detectados automaticamente", expanded=True):
            detection = json.loads(detection_file.read_text(encoding="utf-8"))
            st.dataframe([{"Instrumento": data["label"], "Índice do detector": f"{data['score']:.3f}" if data.get("score") is not None else "Já separado",
                           "Faixa gerada": kind in detection["exported"]}
                          for kind, data in detection["presence"].items()], hide_index=True)
            if detection.get("automatic_stems_rechecked"):
                st.caption("Faixas automáticas de piano/guitarra também foram reanalisadas pelo áudio, sem confiar apenas no nome.")
            if detection.get("validation"):
                with st.expander("Conferência dos timbres separados"):
                    st.json(detection["validation"])
            if detection.get("preserved"):
                st.caption("Faixas preservadas: " + ", ".join({"piano": "Piano", "guitar": "Guitarra / violão", "accordion": "Sanfona", "acoustic_guitar": "Violão", "electric_guitar": "Guitarra elétrica"}.get(kind, kind) for kind in detection["preserved"]))
            if detection.get("errors"):
                st.warning("A identificação ou a separação adicional não terminou. As partes disponíveis foram preservadas; consulte o relatório para detalhes.")
            if detection.get("fallback"):
                st.warning("Parte do acompanhamento não foi identificada com segurança. Ela foi mantida em other.mid para revisão, com um timbre genérico.")
            st.caption("O índice não é uma probabilidade calibrada. Confira o áudio e o MIDI: há possibilidade de falsos positivos e instrumentos não detectados.")
            leftover = Path(result["result_dir"]) / "acompanhamento_nao_identificado.wav"
            if leftover.is_file():
                st.download_button("Baixar áudio restante (não transcrito)", leftover.read_bytes(), leftover.name, mime="audio/wav")
            st.download_button("Baixar relatório de instrumentos", detection_file.read_bytes(), "instrumentos.json", mime="application/json")
    midi_dir = Path(result["midi_dir"])
    midi_files = sorted(midi_dir.glob("*")) if midi_dir.exists() else []
    metric3.metric(
        "Arquivos MIDI/KAR", str(sum(1 for f in midi_files if f.suffix.lower() in (".mid", ".kar")))
    )

    from stem_mixer import show_stem_mixer
    show_stem_mixer(st, result)

    with st.expander("🎚️ Ajustar volumes (sem converter de novo)"):
        with st.form("remix_" + str(st.session_state.get("last_job_id", "job"))):
            revised_mix = mix_controls(
                "saved_mix_" + str(st.session_state.get("last_job_id", "job")),
                result.get("mix_settings"),
            )
            if st.form_submit_button("Aplicar volumes"):
                try:
                    remix_result(result, revised_mix)
                    st.rerun()
                except Exception as exc:
                    st.error("Não consegui atualizar a mixagem: " + str(exc))

    if result.get("transcription_warning"):
        st.warning(result["transcription_warning"])
    preferred = [
        "karaoke.kar",
        "karaoke.mid",
        "completo.mid",
        "instrumental.mid",
        "piano_solo.mid",
        "piano.mid",
        "bass.mid",
        "drums.mid",
        "vocals.mid",
        "guitar.mid",
        "other.mid",
    ]
    by_name = {f.name: f for f in midi_files if f.is_file()}
    displayed = [by_name[name] for name in preferred if name in by_name]
    displayed.extend(
        f
        for f in midi_files
        if f.is_file() and f not in displayed and f.suffix.lower() in (".mid", ".kar")
    )
    def download_file(file, key_prefix, **kwargs):
        mime = "audio/midi" if file.suffix.lower() in (".mid", ".kar") else "application/octet-stream"
        st.download_button(
            f"⬇️ {file.name}",
            data=file.read_bytes(),
            file_name=file.name,
            mime=mime,
            on_click="ignore",
            key=f"{key_prefix}_{st.session_state.get('last_job_id', 'job')}_{file.name}",
            width="stretch",
            **kwargs,
        )

    main_names = ("karaoke.mid", "karaoke.kar", "completo.mid")
    main_files = [by_name[name] for name in main_names if name in by_name]
    other_files = [f for f in displayed if f not in main_files]
    if main_files:
        main_cols = st.columns(len(main_files))
        for column, file in zip(main_cols, main_files):
            with column:
                download_file(file, "main", type="primary")
    if other_files:
        with st.expander(f"Faixas individuais ({len(other_files)})"):
            download_cols = st.columns(2)
            for idx, file in enumerate(other_files):
                with download_cols[idx % 2]:
                    download_file(file, "dl")

    samples_report = Path(result["result_dir"]) / "samples.json"
    if samples_report.is_file():
        sample_data = json.loads(samples_report.read_text(encoding="utf-8"))
        if sample_data.get("errors"):
            st.warning("Alguns áudios com samples não foram gerados. Os MIDIs estão salvos.")
            with st.expander("Detalhes da geração de áudio"):
                st.write(sample_data["errors"])
        with st.expander("🎵 Ouvir e baixar com samples"):
            for filename in sample_data.get("files", []):
                audio_file = Path(result["result_dir"]) / "audio_samples" / Path(filename).name
                if audio_file.is_file():
                    st.write(audio_file.stem)
                    st.audio(str(audio_file))
                    st.download_button("Baixar " + filename, audio_file.read_bytes(), filename,
                                       mime="audio/mpeg", key="samples_" + str(audio_file))
    st.info("Arquivos salvos em: " + str(current_job))

    with st.expander("🎹 Tocar o MIDI com os samples incluídos"):
        from sample_audio import start_midi_playback, SOUNDFONT
        current_player = st.session_state.get("midi_sample_player")
        playable = [file for file in displayed if file.suffix.lower() in (".mid", ".kar")]
        if playable:
            selected_midi = st.selectbox("MIDI para ouvir", playable, format_func=lambda p: p.name)
            if st.button("▶ Tocar MIDI com samples"):
                if current_player is not None and current_player.poll() is None:
                    current_player.terminate()
                try:
                    player = start_midi_playback(selected_midi, Path(result["result_dir"]) / "player_midi.log")
                    st.session_state["midi_sample_player"] = player
                    current_player = player
                    time.sleep(0.15)
                    if player.poll() not in (None, 0):
                        st.error("Não foi possível iniciar o áudio. Confira o dispositivo de saída do Windows.")
                    else:
                        st.success("MIDI enviado ao player com GeneralUser GS.")
                except (OSError, RuntimeError) as exc:
                    st.error(str(exc))
            if st.button("■ Parar MIDI") and current_player is not None:
                if current_player.poll() is None:
                    current_player.terminate()
                st.session_state.pop("midi_sample_player", None)
        st.caption("O áudio sai no computador onde o aplicativo está aberto. O banco é carregado pelo player; não fica embutido no .mid. Para usar estes timbres em outro player, configure nele o banco abaixo.")
        st.download_button("Baixar banco de samples SF2", SOUNDFONT.read_bytes(), SOUNDFONT.name,
                           mime="application/octet-stream")
    harmony_path = Path(result["result_dir"]) / "relatorio_harmonia.json"
    if harmony_path.is_file():
        harmony_data = json.loads(harmony_path.read_text(encoding="utf-8"))
        st.caption("Escala aplicada a todos os instrumentos melódicos: " + harmony_data["key"])
        conflicts = harmony_data["files"].get("completo.mid", {}).get("chords_outside_scale", [])
        if conflicts:
            st.warning("Cifras com notas fora da escala escolhida foram limitadas à escala: " + ", ".join(sorted(set(conflicts))))
        st.download_button("Baixar relatório de harmonia", harmony_path.read_bytes(), harmony_path.name,
                           mime="application/json")

    reports_box = st.expander("📋 Relatórios da conversão")
    with reports_box:
        cleanup_report_path = Path(result["result_dir"]) / "relatorio_limpeza.json"
        if cleanup_report_path.is_file():
            cleanup_data = json.loads(cleanup_report_path.read_text(encoding="utf-8"))
            for track in cleanup_data.get("tracks", []):
                label = {"bass": "Baixo", "guitar": "Guitarra / violão"}.get(
                    track["stem"], track["stem"]
                )
                st.caption(
                    f"Limpeza — {label}: {track['before']} → {track['after']} notas; máximo simultâneo: {track['max_polyphony_before']} → {track['max_polyphony_after']}."
                )
            st.download_button(
                "Baixar relatório da limpeza automática",
                cleanup_report_path.read_bytes(),
                file_name="relatorio_limpeza.json",
                mime="application/json",
                on_click="ignore",
            )

        live_report = Path(result["result_dir"]) / "relatorio_voz.json"
        if live_report.is_file():
            report_data = json.loads(live_report.read_text(encoding="utf-8"))
            st.caption(
                f"Filtro vocal: {len(report_data.get('rejected_or_trimmed', []))} notas removidas ou recortadas. Ouça a guia vocal para revisar."
            )
            st.download_button(
                "Baixar relatório do filtro vocal",
                live_report.read_bytes(),
                file_name="relatorio_voz.json",
                mime="application/json",
                on_click="ignore",
            )

    archive = Path(result["archive"])
    if archive.exists():
        size_mb = archive.stat().st_size / 1048576
        prepare_zip = size_mb <= 20 or st.checkbox(
            f"Preparar ZIP completo para download ({size_mb:.1f} MB)", value=False
        )
        if prepare_zip:
            st.download_button(
                "⬇️ Baixar pacote completo (ZIP)",
                data=archive.read_bytes(),
                file_name="entrega_midi_karaoke.zip",
                mime="application/zip",
                on_click="ignore",
            )
        else:
            st.caption(
                "Os MIDIs individuais acima já podem ser baixados sem carregar o ZIP grande na memória."
            )

    with st.expander("✏️ Editor de letra e sincronização"):
        st.caption(
            "Tempos em segundos desde o início da música. Use [QUEBRA] para mudar de linha e preserve os espaços entre palavras."
        )
        source = Path(result["source_audio"])
        if source.is_file() and st.checkbox("Ouvir um trecho de áudio", value=False):
            start_at = st.number_input(
                "Ouvir a partir de (segundos)",
                min_value=0,
                max_value=max(0, int(float(result.get("duration", 0))) - 1),
                value=0,
                step=10,
            )
            try:
                preview = audio_preview(source, result["job_dir"], start=start_at)
                st.audio(str(preview), format="audio/mp3")
                st.caption(
                    f"Prévia de até 30 segundos a partir de {start_at}s; os tempos do editor continuam relativos ao início da música."
                )
            except Exception as exc:
                st.warning(str(exc))
        events_file = Path(result["events_path"])
        rows = json.loads(events_file.read_text(encoding="utf-8"))
        edited = st.data_editor(
            rows or [{"seconds": 0.0, "text": ""}],
            num_rows="dynamic",
            column_config={
                "seconds": st.column_config.NumberColumn("Início (s)", min_value=0.0, step=0.01),
                "text": st.column_config.TextColumn("Sílaba / [QUEBRA]"),
            },
            key="editor_" + st.session_state["last_job_id"],
            width="stretch",
        )
        if st.button("Salvar tempos e gerar KAR novamente"):
            try:
                revise_lyrics(result, edited)
                st.session_state["last_result"] = result
                st.session_state["lyrics_saved"] = True
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if st.session_state.pop("lyrics_saved", False):
            st.success("KAR, MIDI e ZIP atualizados com os tempos revisados.")
        st.download_button(
            "Baixar tempos das sílabas (JSON)",
            events_file.read_bytes(),
            "silabas.json",
            "application/json",
            on_click="ignore",
        )

    if result.get("karaoke_kar"):
        st.info(
            "O arquivo karaoke.kar contém uma pista de letra MIDI. Os tempos das sílabas são aproximações automáticas; confira no VanBasco antes de usar em apresentação."
        )
    elif result.get("transcription_warning"):
        st.warning(
            "O MIDI instrumental foi criado, mas a transcrição da letra falhou: "
            + str(result["transcription_warning"])
        )
    else:
        st.warning(
            "Não foram gerados eventos de letra. A música pode não ter voz detectável ou o modelo não reconheceu palavras suficientes."
        )

    st.caption(
        "Mantenha a janela do programa aberta para baixar. Os arquivos também ficam salvos no computador."
    )
    st.caption("Pasta desta execução: " + str(result.get("job_dir", "")))
    lyrics_path = result.get("lyrics_txt")
    if lyrics_path and Path(lyrics_path).exists():
        with st.expander("Prévia da letra reconhecida"):
            st.text(Path(lyrics_path).read_text(encoding="utf-8", errors="replace"))
    if st.button("Limpar resultado exibido"):
        st.session_state.pop("last_result", None)
        st.session_state.pop("last_job_id", None)
        st.rerun()

with st.expander("ℹ️ Sobre e diagnóstico"):
    st.markdown(
        """
        - **Demucs** separa a música em instrumentos; **Basic Pitch** estima as notas. Timbres distorcidos e mixagens densas podem gerar erros.
        - **Bateria** vem da análise de ataques e frequências; não é uma transcrição humana.
        - **Letra** é reconhecida pelo Whisper e dividida em sílabas; o karaokê pode precisar de ajustes.
        - **MIDI** Type 1, PPQ 960, bateria no canal 10, andamento constante.
        - **Qualidade** sempre no máximo: Demucs htdemucs_ft, Whisper medium, conferência de timbre e notas sem reescrita por escala ou cifra.
        - Processamento local; a primeira execução pode baixar modelos de IA e demorar.
        """
    )
    st.write("Python:", sys.version.split()[0])
    st.write("FFmpeg:", shutil.which("ffmpeg") or "Não encontrado no PATH")
    st.write("FFprobe:", shutil.which("ffprobe") or "Não encontrado no PATH")
    for package in ["demucs", "torch", "torchaudio", "basic-pitch", "faster-whisper"]:
        try:
            st.write(package + ": " + str(version(package) or "versão indisponível"))
        except PackageNotFoundError:
            st.write(package + ": não instalado")
