# Como entender o código

O código editável fica na pasta `programa`. O arquivo `MidiDream.exe` é o iniciador: ele prepara o ambiente e abre a interface. Para estudar a conversão, comece pelos arquivos Python abaixo.

## Ordem sugerida de leitura

| Arquivo | Responsabilidade |
| --- | --- |
| `app.py` | Desenha a tela, recebe arquivos e valida BPM/letra com cifras. O bloco `if start` inicia o processamento. |
| `pipeline.py` | Coordena as etapas: entrada, separação, conversão, letra, mixagem e ZIP. A função principal é `run_pipeline`. |
| `audio2midi_core.py` | Escolhe o detector de cada instrumento e gera as faixas MIDI. A função `main` mostra a sequência completa. |
| `accompaniment_cleanup.py` | Filtra notas fracas do acompanhamento e aplica correções de até um semitom com contexto harmônico. |
| `drum_classifier.py` | Distingue ressonância dos tons e ruído da caixa, incluindo esteira atenuada pelo demixing. |
| `drum_timing.py` | Alinha a bateria à grade, une ataques duplicados e separa a faixa de chimbal. |
| `rhythm_lock.py` | Simplifica baixo e violão pela grade. Não é mais usado pelo perfil fixo. |
| `quality.py` | Perfil único de máxima qualidade (modelos, filtros e opções fixas). |
| `vocal_melody.py` | Extrai uma linha de melodia da voz isolada para tocar na flauta. |
| `lyric_alignment.py` | Localiza no áudio as palavras da letra fornecida. |
| `lyrics.py` | Divide palavras em sílabas e grava os eventos de letra no MIDI/KAR. |
| `music_guide.py` | Lê cifras como `Am`, `G7M` ou `[Am]`, valida os campos obrigatórios e cria acompanhamento opcional por acordes. |

## O caminho de uma música

1. A interface exige o BPM e a letra com cifras. `quality.py` fixa todos os parâmetros de qualidade.
2. `pipeline.py` recebe o áudio. Se forem stems prontos, pula a separação.
3. O motor identifica notas e ataques. Bateria, voz e instrumentos polifônicos usam métodos diferentes.
4. `rhythm_lock.py` simplifica baixo e violão; a melodia vocal mantém os tempos detectados.
5. A letra é sempre alinhada ao canto, usando a faixa vocal separada. As sílabas dentro de uma palavra são distribuídas pelo intervalo dela.
6. O programa aplica os volumes e oferece os arquivos MIDI/KAR e o ZIP.

## Nomes que aparecem nos cálculos

- **Stem:** áudio de um instrumento/grupo separado, como voz ou baixo.
- **`start` e `end`:** início e fim de uma nota em segundos, nos objetos `pretty_midi`.
- **`pitch`:** número da altura da nota MIDI; não é volume.
- **`velocity`:** intensidade do ataque da nota, de 1 a 127. O controle geral de volume é tratado separadamente.
- **BPM:** quantidade de batidas por minuto. Uma batida dura `60 / bpm` segundos.
- **PPQ:** quantidade de ticks por batida na gravação do MIDI.
- **`sr`:** taxa de amostragem do áudio, em amostras por segundo.
- **`hop`:** distância, em amostras, entre dois quadros de análise. `hop / sr` converte essa distância para segundos.
- **`f0`:** frequência fundamental estimada da voz, em Hz.
- **`rms`:** medida da energia do som, usada para separar sinais fortes de trechos muito fracos.
- **`n`, `inst` e `t`:** abreviações locais de nota, instrumento e instante.

A biblioteca `mido` grava tempos relativos em ticks: o campo `time` de uma mensagem indica quanto esperar desde a mensagem anterior. `midi_timing.py` faz a conversão entre segundos e ticks respeitando o andamento. A bateria usa o canal MIDI 10, representado pelo índice `9` no Python.

## Arquivos de apoio

`musical_style.py` aplica estatísticas da referência; `note_cleanup.py` e `live_vocals.py` filtram notas; `midi_mix.py` controla volumes; `performance.py` cuida de cache e uso de CPU. Os arquivos com `worker` executam tarefas pesadas em processos separados. `saved_results.py`, `song_names.py` e `job_paths.py` cuidam do histórico e das pastas.

A pasta `tests` contém exemplos pequenos e verificações automáticas. Os comentários explicam as decisões e as unidades dos cálculos, sem comentar toda atribuição. A organização e os comentários foram mantidos. Os ajustes da bateria desta versão estão descritos em `documentacao/MELHORIAS_3.0.md`.
