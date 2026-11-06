# Versão 4.3

- Escala explícita obrigatória na interface e na CLI (`--key "C maior"`).
- Correção final de todas as faixas melódicas, antes da criação do KAR e das cópias para mixagem.
- Notas da escala e solos diferentes são preservados; cifras desempatarão correções igualmente próximas.
- Canal 10 de bateria preservado; pitch bends melódicos neutralizados.
- Relatório de correções e conflitos entre cifras e escala.
- Reanálise dos stems automáticos de piano/guitarra e validação do áudio extraído.
- Respeita rótulos das faixas isoladas fornecidas pelo usuário.
- Rejeita a mesma forma de onda vazando para dois rótulos, priorizando a maior evidência do detector.
- Cache do separador atualizado para invalidar resultados do procedimento antigo.
- Mixer de áudio com Solo, Mute e volume, inspirado no fluxo do Moises, sem usar seu motor.
- Reprodução MIDI direta com FluidSynth e GeneralUser GS; play/stop e download do banco SF2.

Validação: testes de escalas, canais, bateria, Note Off através de trocas de acorde,
solos, exportação KAR e remixagem; regressão de identificação sanfona/piano com
detector simulado; comando do player e mixer real com FFmpeg; interface por AppTest.
A precisão do classificador não foi medida em um conjunto de músicas reais.
O player nativo não foi executado em Windows neste ambiente.
