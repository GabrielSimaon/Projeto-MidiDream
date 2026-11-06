# MidiDream

Aplicativo Python local com interface no navegador e CLI. Baseado nos arquivos enviados, com correções de timing, canais, bateria e exportação instrumental.

**Fluxo:** link YouTube ou áudio → yt-dlp/FFmpeg → Demucs → Basic Pitch + DSP de bateria → MIDI Tipo 1 → faster-whisper + divisão silábica → `.mid` e `.kar`.

## Começar no Windows

1. Instale **Python 3.11 x64**, com Python Launcher, e **FFmpeg/FFprobe** no PATH.
2. Para YouTube, instale **Deno**, usado pelo yt-dlp para resolver desafios JavaScript. O pacote `yt-dlp[default]` inclui os componentes Python/EJS necessários.
3. Descompacte o ZIP; execute **instalar_windows.bat**. A instalação de CPU baixa várias dependências grandes.
4. Execute **iniciar_windows.bat**.
5. Cole o link de uma música, selecione as opções e clique em **PROCESSAR MÚSICA**.
6. Baixe o ZIP final ou `karaoke.kar`. Abra no VanBasco e revise o resultado.

Com Windows Package Manager, FFmpeg e Deno podem ser instalados assim:

```powershell
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Abra um terminal novo após instalar. Verifique `ffmpeg -version`, `ffprobe -version` e `deno --version`.

Instalação manual:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip wheel
.venv\Scripts\python.exe -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Linux/macOS: use Python 3.11 em um venv, instale FFmpeg e Deno com o gerenciador do sistema; depois instale `requirements.txt` e execute `python -m streamlit run app.py --server.address 127.0.0.1`. O instalador BAT destina-se ao Windows; os outros sistemas não foram validados aqui.

## Controles da interface

A qualidade não é configurável: o perfil fixo está em `quality.py` (Demucs `htdemucs_ft`, Whisper `medium`, reconhecimento de instrumentos com conferência de timbre, filtro de notas fantasma, MIDI com PPQ 960). Sobram apenas escolhas que mudam o resultado final, não a precisão:

- **BPM:** obrigatório. Vale para o MIDI; a grade rítmica da bateria é calibrada no áudio (período e fase medidos perto do BPM informado).
- **Melodia vocal:** Choir Aahs ou Flute. O arquivo instrumental exclui essa pista.
- **Letra:** idioma, texto com cifras, JSON/KAR já sincronizado. A letra é alinhada ao canto com stable-ts sobre a faixa vocal separada.
- **Volumes**, **MIDI de referência**, **filtro de plateia (gravação ao vivo)** e **WAVs no ZIP** continuam opcionais.
- **Editor de sílabas:** ouça o áudio, ajuste os tempos em segundos e o texto; salve para reexportar KAR/MIDI/ZIP sem repetir inferência. Os espaços ao final de palavras devem ser preservados.

Para stems existentes, envie arquivos como `vocals.wav`, `bass.wav`, `drums.wav`, `piano.wav`, `guitar.wav`, `other.wav`. Eles devem começar no mesmo instante da música, sem remover silêncios iniciais. São necessários dois ou mais nomes reconhecidos para o modo multistem. Um único stem pode ser enviado com a separação desligada. Duplicatas da mesma categoria são rejeitadas para evitar perder faixas silenciosamente.

## Arquivos gerados

| Arquivo | Conteúdo |
|---|---|
| `completo.mid` | Todos os instrumentos e melodia vocal, sem letra |
| `instrumental.mid` | Acompanhamento sem melodia vocal |
| `karaoke.mid`, `karaoke.kar` | Instrumentos, guia vocal e metadados de letra |
| `bass.mid`, `drums.mid`, etc. | Faixas individuais detectadas |
| `silabas.json` | Sílabas e quebras com posições em segundos; pode ser reimportado |
| `palavras.json` | Palavras e timestamps do reconhecimento |
| `letra_reconhecida.txt` | Texto da letra |
| `log_transcricao.txt` | Parâmetros, avisos e contagens de notas |

`trabalhos/job_.../entrega_midi_karaoke.zip` contém os resultados e, opcionalmente, os WAVs dos stems. Áudio intermediário e modelos ocupam espaço e não são apagados automaticamente.

### Canais General MIDI

Os números abaixo são os exibidos aos músicos. O Python/mido usa canais começando em zero; programas também são zero-based.

| Pista | Canal | Programa GM (humano) |
|---|---:|---|
| Baixo | 1 | 34 — Electric Bass (finger) |
| Guia vocal | 2 | 53 — Choir Aahs / 74 — Flute |
| Piano | 3 | 1 — Acoustic Grand Piano |
| Guitarra | 4 | 26 — Steel String Guitar |
| Outros | 5 | 1 — Acoustic Grand Piano (timbre genérico) |
| Bateria | 10 | Percussão: 36 bumbo, 38 caixa, 42 chimbal, 49 crash |

O programa GM para `other` é uma escolha de reprodução, não uma identificação dos instrumentos daquela faixa. General MIDI só padroniza a bateria no canal 10; os demais canais são convenções deste aplicativo.

### Letra e compatibilidade

Eventos **FF 05 (`lyrics`)**, Latin-1 para acentos comuns do português e quebras **CRLF (`\r\n`)** reais. Não são gravados `/` ou `\` como marcadores visíveis. `[QUEBRA]` é apenas uma representação do editor/JSON, convertida para CRLF ao exportar. A letra fica em uma pista própria; os tempos são convertidos usando o mapa completo de andamento do MIDI.

Um `.kar` é um Standard MIDI File com extensão KAR. Diferentes leitores legados usam convenções diferentes de texto/controle de tela. A estrutura MIDI/CRLF foi testada; **a exibição no aplicativo VanBasco não foi validada neste ambiente**. Não há garantia de comportamento idêntico entre versões. O formato pedido evita deliberadamente os marcadores de barras de outras variantes de Soft Karaoke.

A divisão linguística usa Pyphen. **O tempo de cada sílaba é interpolado dentro da palavra reconhecida, não medido por alinhamento fonético.** Texto colado é associado às palavras reconhecidas e interpolado nos trechos ausentes. Sem reconhecimento, distribui-se pela duração e exibe-se aviso; esse caso exige ajuste. JSON revisado preserva os tempos fornecidos, sem interpolação.

Exemplo de JSON (tempos ilustrativos, não vinculados a uma música):

```json
[
  {"seconds": 12.3, "text": "Co"},
  {"seconds": 12.55, "text": "ra"},
  {"seconds": 12.9, "text": "ção "},
  {"seconds": 13.8, "text": "[QUEBRA]"}
]
```

## Automatização por terminal

Com o venv ativo:

```bash
python cli.py --audio musica.wav --lyrics letra.txt --bpm 120 -o trabalhos/musica_local
python cli.py --audio musica.wav --lyrics letra.txt --bpm 120 --timed-lyrics silabas.json -o trabalhos/letra_revisada
python cli.py --audio stems/bass.wav --stems stems --lyrics letra.txt --bpm 120 --no-lyrics -o trabalhos/stems_prontos
```

Use uma pasta de saída nova a cada conversão. `--include-stems` adiciona os WAVs ao ZIP. `python cli.py --help` lista as opções.

## Organização do código

- `app.py`: interface Streamlit, upload/download e editor de timestamps.
- `cli.py`: automação por terminal com a mesma pipeline.
- `quality.py`: perfil fixo de máxima qualidade usado pela interface e pelo terminal.
- `pipeline.py`: download, Demucs, orquestração, exportação e reexportação da letra.
- `audio2midi_core.py`: Basic Pitch polifônico, filtros por instrumento e bateria espectral.
- `lyrics.py`: reconhecimento Whisper, divisão silábica e metadados de letra.
- `midi_timing.py`: mapa de tempo, conversão segundos/ticks, PPQ e instrumental.
- `tests/test_studio.py`: validação temporal e de integração sem downloads de modelos.

As notas não são forçadas para uma tonalidade fixa. Reataques próximos da mesma nota são preservados; apenas sobreposições da mesma altura são fundidas. O modelo Basic Pitch é carregado uma vez por execução do motor. Não há preenchimento inventado de chimbal; a bateria vem exclusivamente das detecções.

## Limites e solução de problemas

**Não existe conversão perfeita de qualquer música.** Separação, alturas, ataques, bateria e reconhecimento do canto são estimativas. Basic Pitch funciona melhor com um instrumento de cada vez; instrumentos distorcidos, coros e misturas densas podem produzir notas falsas ou omissões. O detector de bateria é uma heurística de bandas espectrais, não um modelo dedicado de transcrição; não cobre todos os instrumentos de percussão. MIDI armazena notas e controles; não carrega o timbre da gravação.

**YouTube bloqueou o download:** atualize com `python -m pip install --upgrade "yt-dlp[default]"`, confira Deno e FFmpeg e tente um vídeo acessível publicamente. Vídeos privados, removidos, restritos por região/idade ou desafios do site podem impedir o download. O aplicativo não garante suporte a todos os links. Envie o áudio local como alternativa.

**Lento/pouca RAM:** use Demucs de quatro faixas, Whisper `tiny`/`base` ou desligue o reconhecimento. CPU pode demorar mais que a própria música. Feche outros aplicativos; uma máquina com 8 GB pode precisar de memória virtual e ainda falhar em músicas longas. Modelos e stems exigem espaço em disco e internet para o primeiro download.

**Erro de instalação:** use Python 3.11 x64 e ambiente novo; não misture as dependências do sistema. O par PyTorch/torchaudio é fixado em 2.5.1 para evitar alterações incompatíveis de I/O/carregamento no Demucs antigo. Execute `python -m pip check`. TensorFlow/ONNX e as bibliotecas de IA também dependem de suporte do sistema e CPU.

**Sincronia:** mantenha todos os stems alinhados ao mesmo zero; desative a quantização; confira a letra e o áudio no editor. O código temporal suporta mapas MIDI com mudanças de andamento, mas o estimador de áudio desta versão usa BPM constante. Não confunda precisão temporal do MIDI com acurácia da transcrição.

Use áudio cujo download/processamento seja autorizado. O app não contorna DRM ou paywalls.

## Validação

```bash
python -m unittest discover -s tests -v
```

Testes com MIDI e áudio sintético cobrem mudança de andamento, conversão PPQ sem deriva acumulada, canais GM, retirada da guia vocal, reexportação de letras sem duplicatas, acentos/CRLF, timestamps inválidos, silêncio na bateria, reataques e fluxo de empacotamento/revisão com inferência simulada. Não substituem uma conversão musical real com os modelos e o VanBasco no Windows.

## Referências oficiais

- Demucs e modelo experimental de seis fontes: https://github.com/facebookresearch/demucs
- Basic Pitch e API de inferência: https://github.com/spotify/basic-pitch
- faster-whisper e timestamps por palavra: https://github.com/SYSTRAN/faster-whisper
- yt-dlp e runtime JavaScript: https://github.com/yt-dlp/yt-dlp/wiki/EJS
- Mido e tempo MIDI: https://mido.readthedocs.io/en/stable/files/midi.html
