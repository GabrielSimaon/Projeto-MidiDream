# Versão 4.8 — Identidade instrumental fiel ao áudio

Esta versão prioriza o instrumento realmente presente no MP3/stem e deixa de
fabricar acompanhamentos que não foram detectados.

## Identidade e timbre

- Cada stem mantém o programa General MIDI correspondente à sua identificação:
  baixo, bateria, voz, piano, violão, guitarra elétrica, sanfona e `other`.
- A mixagem final preserva esses programas; o controle de volume não transforma
  automaticamente instrumentos detectados em piano.
- O auto-mix reconhece os instrumentos extras pelos nomes das trilhas, evitando
  confundir sanfona ou guitarra com o segundo piano.
- A reprodução continua usando `programa/samples/GeneralUser-GS.sf2`.

## Sem instrumentos fantasmas

- O processamento principal usa o modo estrito do aprimorador de teclados:
  piano, pad e violão só recebem notas quando existe atividade detectada no stem.
- Introduções, pausas, solos e trechos sem presença do instrumento ficam em
  silêncio, em vez de receberem acordes sintéticos baseados apenas na cifra.
- Um acompanhamento que não puder ser identificado continua em `other`, sem ser
  forçado para piano ou guitarra.

## Limite dos arquivos MIDI de referência

Arquivos `.mid` armazenam notas, tempos, canais, programas e controles MIDI; não
armazenam os samples de áudio usados pelo sintetizador. Os MIDIs de referência
podem orientar estrutura, dinâmica e instrumentação, mas sons específicos só
podem ser incorporados a partir de áudio ou de um banco de samples autorizado.
