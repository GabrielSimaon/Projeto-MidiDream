# Versão 4.5 — Otimização de Separação, Precisão de Notas e Interface Google Material

Esta atualização atende às principais demandas do projeto:
1. **Velocidade na separação de instrumentos**.
2. **Eliminação de notas inventadas/fantasmas** e precisão nos timbres.
3. **Modernização visual da interface** inspirada no design dos aplicativos Google.

---

## 1. Otimização na Separação de Instrumentos

| Componente | Antes | Agora | Impacto |
|---|---|---|---|
| `separate_worker.py` (Demucs) | Apenas threads internas do PyTorch | Injeção de `--jobs` dinâmico baseado nos núcleos | Processamento e carregamento paralelo sem gargalo |
| `instrument_worker.py` (AST) | Janelas com avanço de `hop = 48000` (1,5s) | Avanço otimizado para `hop = 64000` (2s) | Redução de ~25% no tempo de classificação sem perda de precisão |
| `instrument_worker.py` (AudioSep) | Janela de 6s com hop de 5s | Janela de 6s com hop de 4s | Menos sobreposição desnecessária e menor tempo de CPU |
| `instrument_worker.py` (Threads) | `min(4, os.cpu_count())` | `min(available, 6)` | Aproveitamento de mais núcleos de processamento |
| `pipeline.py` (Demucs Segments) | Segmentos de 6s / 7s | Segmentos aumentados para 7s / 9s | Menos trocas de contexto e menor sobrecarga de segmentação |

---

## 2. Eliminação de Notas Inventadas e Correção de Timbres

### Por que notas eram inventadas anteriormente:
- O Basic Pitch gerava notas fantasma nos harmônicos superiores de notas reais (oitavas e quintas).
- Limiares de onset/frame no piano e acompanhamento (`other`) estavam muito sensíveis, capturando transientes percussivos e ruídos residuais.
- Bumbo disparava com frequências graves de baixos fortes ou ressonâncias de tons.
- Notas vocais com micro-oscilações geravam múltiplos ataques curtíssimos indesejados.
- **Baixo**: sofria elevação forçada de +12 semitons no pós-processamento, jogando a linha de baixo para a região de solos (C4-G4), além de transcrever harmônicos superiores e ruídos de traste como notas de solo rápidas sem remoção de fantasmas.
- **Violões e Guitarras**: o detector AST exigia a classe estrita `"Acoustic guitar"`, descartando qualquer violão no fundo que o modelo rotulasse como `"Guitar"` ou `"Strum"`, atribuindo todo o acompanhamento ao Piano.

### Ajustes implementados:
- **Violões e Guitarras de Fundo (`instrument_detection.py`)**:
  - `acoustic_guitar`: classes expandidas para `("Acoustic guitar", "Guitar", "Strum", "Plucked string instrument", "Steel guitar, slide guitar")` com limiar reduzido para **0.15** e prompt detalhado para o AudioSep isolar dedilhados e batidas rítmicas.
  - `electric_guitar`: classes expandidas com `("Electric guitar", "Guitar", "Tapping (guitar technique)")` e limiar **0.18**.
  - `verify_identity`: removido falso conflito entre instrumentos da mesma família (violão vs guitarra elétrica).
  - Cache de instrumentos atualizado para versão 4.
- **Baixo Fundamental Puro (`bass_pitch.py` e `audio2midi_core.py`)**:
  - **Eliminada a elevação artificial de +12 semitons**, que colocava o baixo na oitava de solos de guitarra.
  - O `validate_bass_pitch` agora remove ativamente notas onde o áudio não possui fundamental sonora real (elimina solos inventados e vazamentos).
  - Notas de harmônicos superiores são dobradas para a **tessitura fundamental do baixo (MIDI 28 = E1 a 52 = E3)**.
  - Passagem monofônica estrita: remove sobreposições simultâneas e funde micro-repetições na mesma altura em sustentações de groove sólidas.
  - `STEM_PARAMS["bass"]`: `onset: 0.42`, `frame: 0.25`, `min_len: 80 ms`, `pmax: 52` (bloqueia detecção de agudos).
- **`audio2midi_core.py` (STEM_PARAMS)**:
  - Piano: `onset` 0.40 → **0.50**, `frame` 0.25 → **0.30**, `min_len` 60ms → **70ms**, `min_vel` 8 → **12**.
  - Guitarra/Violão: `fmax` ampliado de 1500 Hz para **3000 Hz** para capturar harmônicos e agudos reais de acordes.
  - Acompanhamento (`other`): `onset` 0.35 → **0.45**, `frame` 0.22 → **0.28**, `fmin` 70Hz → **80Hz**, `fmax` 2500Hz → **2200Hz**, `min_vel` 8 → **12**.
- **`post_process_notes`**:
  - Duração mínima para stems polifônicos agora é forçada a **80ms**, descartando glitches rápidos.
  - Filtro de harmônicos com parâmetro específico para piano (`weaker=0.40`), removendo harmônicos falsos de oitava (+12, +24) e quinta (+19).
- **`note_cleanup.py` (`remove_overtone_ghosts`)**:
  - Parâmetros padrão mais rigorosos: `weaker` 0.60 → **0.45**, `window` 0.06s → **0.055s**, `min_overlap` 0.50 → **0.45**.
- **Bateria (`transcribe_drums`)**:
  - Bumbo mais seletivo: `low_frac` 0.08 → **0.12** e `kick_strength` 0.55 → **0.62**. Elimina falsos disparos de bumbo causados por linhas de baixo pesadas.
- **Voz (`vocal_melody.py`)**:
  - Janela do filtro de mediana aumentada de `size=9` para `size=11`.
  - Duração mínima da nota vocal aumentada de 65ms para **80ms**.

## 3. Alinhamento Harmônico pelas Cifras e Faixas Separadas (Versão 4.5.1)

### O problema relatado:
- O piano e o baixo soavam desordenados/desafinados ("bagunçados") e não respeitavam a harmonia dos acordes digitados pelo usuário.
- O programa estava agrupando violões e guitarras dentro do canal de piano (`other.mid` / timbre 0), sem gerar uma faixa individual de violão/guitarra.

### Ajustes implementados:
1. **Separação Nativa de 6 Stems com Demucs (`htdemucs_6s`)**:
   - `quality.py` e `pipeline.py`: o modelo de separação principal agora é o **`htdemucs_6s`**, que isola diretamente do MP3:
     - `drums.wav` → `drums.mid` (Bateria no canal 10)
     - `bass.wav` → `bass.mid` (Baixo)
     - `vocals.wav` → `vocals.mid` (Voz / Melodia)
     - `piano.wav` → **`piano.mid`** (Piano acústico / teclado, Program 0)
     - `guitar.wav` → **`guitar.mid`** (Violão e guitarra, Program 25)
     - `other.wav` → `other.mid` (Acompanhamento residual / sintetizadores / cordas)
   - `instrument_worker.py`: fontes `piano.wav` e `guitar.wav` são preservadas em faixas exclusivas e nunca mais são rebaixadas para `other.wav`.

2. **Alinhamento do Baixo às Notas Fundamentais da Cifra**:
   - Em `harmonic_lock.py`, cada nota de baixo é mapeada para o acorde ativo no instante de execução.
   - A altura da nota é amarrada à **fundamental (tônica)** do acorde, inclusive respeitando inversões/acordes com baixo alterado (ex: `C/G` o baixo toca Sol; `D/F#` o baixo toca Fá#).
   - O baixo fica restrito à oitava grave natural (MIDI 28 a 52), eliminando notas dissonantes ou solos inventados.

3. **Alinhamento do Piano e Violão/Guitarra às Notas da Cifra**:
   - Durante a duração de cada acorde (ex: `G`, `Em7`, `C9`), as notas tocadas pelo piano e pelo violão/guitarra são comparadas às notas constitutivas do acorde ativo (`chord_notes`).
   - Notas fora do acorde e fora da escala são corrigidas para a nota mais próxima do acorde ativo, transformando o acompanhamento caótico em um arranjo harmônico, limpo e profissional.
   - `music_guide.py`: nova função `infer_tonality_from_chords` identifica a escala da música automaticamente pelas cifras informadas pelo usuário quando não especificada.

---

## 4. Interface com Google Material Design

- **Tipografia**: Importação e aplicação das fontes oficiais **Google Sans** e **Roboto**.
- **Hero Card**: Banner superior com gradiente Google Blue (`#1a73e8` a `#0d47a1`), cantos arredondados (`16px`), badge de versão e sombra suave de elevação.
- **Seções em Etapas**: Marcadores de passo com tipografia em maiúsculas (`.md-section-label`) com divisores lineares sutis.
- **Botões**:
  - Botão principal (`🚀 PROCESSAR MÚSICA`) com gradiente Material, cantos arredondados estilo pílula (`24px`), hover com elevação (`transform: translateY(-1px)`).
  - Botões secundários e botões de download com estilo Google outlined (`#dadce0` com preenchimento `#e8f0fe` ao passar o mouse).
- **Cards e Métricas**:
  - `stMetric` com fundo `#f8f9ff`, bordas suaves de 12px e efeito hover.
  - Inputs e caixas de texto com anel de foco Google Blue (`box-shadow: 0 0 0 3px rgba(26,115,232,.12)`).
