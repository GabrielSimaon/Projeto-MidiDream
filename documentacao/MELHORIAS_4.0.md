# MIDI Studio 4.0

- Pasta de saída escolhida pelo usuário, seletor nativo Windows e caminho editável.
- Destino validado e lembrado, histórico lido na pasta selecionada.
- Modo de inserir letra também salva MIDI/KAR em subpasta única no destino.
- Entrada por link do YouTube e reparo de download removidos da interface e CLI.
- Instalador deixa de baixar Deno ou exigir yt-dlp.
- GeneralUser GS 2.0.3 incluído com licença e hash SHA-256 no manifesto.
- FluidSynth 2.6.1 Windows x64 incluído com bibliotecas, licenças e código-fonte disponível em https://github.com/FluidSynth/fluidsynth/tree/v2.6.1.
- Renderização opcional (ativada na interface) de todos os MIDIs canônicos para MP3.
- Bateria no canal 10 e Program Changes são interpretados pelo FluidSynth.
- MP3 individuais e mix completo no ZIP, com player e download na interface.
- Alterar os volumes recria os MP3; falha de áudio é relatada sem perder os MIDIs.

A transcrição continua dependendo da qualidade da separação e da detecção de notas.
MIDI/KAR não incorporam samples. A reprodução externa depende do sintetizador.
