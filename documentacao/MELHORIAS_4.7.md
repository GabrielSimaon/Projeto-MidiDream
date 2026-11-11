# Versão 4.7 — Correção de Ordem dos Instrumentos, Preservação da Métrica de BPM e Fase Rítmica

Esta atualização corrige os problemas de descompasso rítmico (saída do BPM) e desordem na entrada dos instrumentos em introduções e arranjos musicais:

---

## 1. Eliminação da Saída de BPM e Preservação da Âncora de Fase (`rhythm_lock.py`, `drum_timing.py`)

### Causas identificadas:
1. **Grade desancorada da fase real**:
   - O método `snap(t)` em `rhythm_lock.py` utilizava `round(t / step) * step`, ignorando o parâmetro `grid_times` e assumindo que o primeiro tempo da música começava exatamente em `0.000s`.
   - Como músicas gravadas possuem uma âncora de fase (ex: 80 ms a 120 ms de silêncio ou contagem inicial), qualquer arredondamento absoluto por `step` jogava o contrabaixo e o violão fora de fase com a bateria e com o áudio original.
2. **Perda de resolução em andamentos Half-Time (< 95 BPM)**:
   - Em músicas rápidas (150 a 160 BPM) identificadas em half-time (75 a 80 BPM), o passo de colcheia (`step = 60 / bpm / 2`) ficava com 380 ms, o que equivale a uma semínima no andamento real. Qualquer levada em colcheias ou semicolcheias da música original era destruída pelo arredondamento.

### Correções implementadas:
- **`rhythm_lock.py`**:
  - `snap(t)` agora utiliza a grade métrica real de `grid_times` (preservando a âncora de fase e o pulso exato do metrônomo).
  - Em andamentos $< 95$ BPM, a subdivisão é ajustada para semicolcheias (`div = 4.0`), garantindo resolução métrica fiel ao andamento real sem amassar notas rápidas.
  - O arredondamento é delimitado por uma janela de tolerância de até 70% do step, evitando deformar síncopes e swing natural.
- **`drum_timing.py`**:
  - Em andamentos $< 95$ BPM com grade padrão de 16avos, ativa automaticamente `effective_grid = 32`, garantindo que viradas, rufos e ataques rápidos de chimbal mantenham seu alinhamento estrito no tempo.

---

## 2. Fidelidade à Ordem de Entrada dos Instrumentos (`keyboard_enhancer.py`, `bleed_cleaner.py`)

### O que causava a bagunça na introdução:
1. **Sintetização incondicional de instrumentos**:
   - `enhance_acoustic_guitar` gerava levadas de violão desde `0.0s`, mesmo quando o violão só começava a tocar aos 13 segundos.
   - `enhance_other_pad` gerava camas de acordes sustentados desde `0.0s`, cobrindo e embolando a introdução de piano.
   - `enhance_accompaniment_piano` gerava acordes em blocos densos embaixo de solos de piano na introdução quando havia poucas notas de base.
2. **Vazamento acústico precoce (Bleed)**:
   - Resíduos da mão esquerda do piano no áudio eram capturados pelo stem de baixo e convertidos em notas de contrabaixo elétrico aos 6 segundos, antes da entrada real da banda.

### Correções implementadas:
- **`keyboard_enhancer.py`**:
  - **Violão (Acoustic Guitar)**: Identifica o início real da execução do violão (`first_guitar_start`). Intervalos de introdução anteriores à entrada do instrumento permanecem em repouso absoluto.
  - **Cama Harmônica / Pad (Other)**: Não sintetiza pads de acordes durante a introdução instrumental/vocal antes da entrada da banda.
  - **Piano de Acompanhamento vs Piano Solo**: Em intervalos identificados como solo (especialmente na introdução), não injeta blocos de acordes artificiais que soterrem a linha melódica do piano solo.
  - **Ordenação Musical Estrita**: As trilhas no MIDI completo são organizadas seguindo a hierarquia sonora clássica:
    1. `Piano` (Base / Intro)
    2. `Piano Solo` (Solo melódico)
    3. `Drums` (Bateria Kit Completo)
    4. `Drums - Chimbal` (Chimbal e Pratos)
    5. `Acoustic Guitar` (Violão Aço)
    6. `Bass` (Contrabaixo)
    7. `Other` (Pads / Teclados secundários)
    8. `Choir / Vocals` (Voz guia / Melodia)
- **`bleed_cleaner.py`**:
  - Detecta e suprime notas de contrabaixo espúrias na introdução antes do início do groove da bateria que coincidam com notas de piano.
