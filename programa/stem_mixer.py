"""Mixer de conferência dos áudios separados: volume, mute e solo sincronizados."""
import hashlib
import json
from pathlib import Path
import subprocess
import os


def active_stems(files, settings):
    solo = any(settings.get(str(file), {}).get('solo', False) for file in files)
    return [(Path(file), min(150, max(0, settings.get(str(file), {}).get('volume', 100))) / 100)
            for file in files if not settings.get(str(file), {}).get('mute', False)
            and (not solo or settings.get(str(file), {}).get('solo', False))
            and settings.get(str(file), {}).get('volume', 100) > 0]


def mix_preview(files, settings, output_dir, start=0):
    selected = active_stems(files, settings)
    if not selected:
        raise ValueError('Ative pelo menos uma faixa com volume acima de zero.')
    start = max(0, float(start))
    signature = [(str(f.resolve()), f.stat().st_size, f.stat().st_mtime_ns, gain) for f, gain in selected]
    key = hashlib.sha256(json.dumps([signature, start]).encode()).hexdigest()[:20]
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    target = output / ('mix_' + key + '.mp3')
    if target.is_file(): return target
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
    for file, gain in selected:
        command += ['-ss', str(start), '-t', '30', '-i', str(file)]
    filters = [f'[{i}:a]volume={gain}[a{i}]' for i, (_, gain) in enumerate(selected)]
    inputs = ''.join(f'[a{i}]' for i in range(len(selected)))
    filters.append(inputs + f'amix=inputs={len(selected)}:normalize=0:duration=longest,alimiter=limit=0.95:level=false[out]')
    command += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-t', '30', '-ar', '44100',
                '-ac', '2', '-codec:a', 'libmp3lame', '-q:a', '2', str(target)]
    result = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        target.unlink(missing_ok=True)
        raise RuntimeError('Não foi possível preparar o mixer: ' + result.stderr.decode(errors='replace')[-800:])
    return target


def show_stem_mixer(st, result):
    folder = Path(result.get('stems_dir') or '')
    if not folder.is_dir(): return
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {'.wav','.mp3','.flac','.m4a','.ogg'})
    if not files: return
    labels = {'bass':'Baixo','drums':'Bateria','vocals':'Voz','piano':'Piano / teclado',
              'accordion':'Sanfona','acoustic_guitar':'Violão','electric_guitar':'Guitarra elétrica',
              'guitar':'Guitarras','other':'Não identificado / outros'}
    prefix = 'stemmix_' + str(result['job_dir'])
    with st.expander('🎚️ Mixer das faixas separadas', expanded=True):
        st.caption('Confira os timbres no áudio original separado. Solo isola uma ou mais faixas; Mute silencia. Esta prévia não muda as notas do MIDI.')
        settings = {}
        for file in files:
            name, solo, mute, volume = st.columns([2, 1, 1, 2])
            name.write(labels.get(file.stem, file.stem))
            settings[str(file)] = {
                'solo': solo.checkbox('Solo', key=prefix+str(file)+'solo'),
                'mute': mute.checkbox('Mute', key=prefix+str(file)+'mute'),
                'volume': volume.slider('Volume (%)', 0, 150, 100, 5, key=prefix+str(file)+'volume')}
        start = st.number_input('Início da prévia (segundos)', min_value=0,
                                max_value=max(0,int(float(result.get('duration',0)))-1),value=0, key=prefix+'start')
        request = json.dumps([settings, start], sort_keys=True)
        if st.button('Ouvir mixagem (até 30 s)', key=prefix+'play'):
            try:
                preview = mix_preview(files, settings, Path(result['job_dir'])/'previas_mixer', start)
                st.session_state[prefix+'preview'] = (request, str(preview))
            except (OSError, RuntimeError, ValueError) as exc:
                st.error(str(exc))
        generated = st.session_state.get(prefix+'preview')
        if generated and generated[0] == request and Path(generated[1]).is_file():
            st.audio(generated[1], format='audio/mp3')
            st.download_button('Baixar prévia da mixagem', Path(generated[1]).read_bytes(), 'mixagem.mp3', mime='audio/mpeg', key=prefix+'download')
        st.caption('Para exportar as faixas completas, ative “Incluir WAVs dos stems no ZIP” antes de converter.')
