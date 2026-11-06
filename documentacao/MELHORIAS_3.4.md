# Versão 3.4 — interface limpa e piano audível

## Piano “sumido”
O piano e o acompanhamento ficam na faixa **Outros**. Até a 3.3 a opção "Silenciar mistura residual (Outros)" vinha **marcada por padrão**: o programa gravava volume MIDI (CC7) = 0 nessa faixa. As notas continuavam no arquivo, mas o player não as tocava. Foi isso que pareceu "piano removido" no karaoke.kar.

Agora existe o controle **Piano e acompanhamento (%)**, de 0 a 150%, padrão 100%. 0% silencia; acima de 100% reforça as notas, que costumam sair fracas do detector. Resultados antigos que estavam silenciados aparecem com 0% e podem ser corrigidos em "Ajustar volumes (sem converter de novo)".

## Interface
- Sem barra lateral: tudo segue a ordem do trabalho — 1 Música, 2 BPM e letra com cifras, 3 Ajustes, 4 Gerar.
- BPM e letra, que são obrigatórios, saíram do meio das opções e ficam juntos no passo 2.
- Os ajustes ficam recolhidos e agrupados em abas: Qualidade, Instrumentos, Bateria, Voz e letra, Volumes e Mais. Os valores padrão não mudaram.
- Resultados: baixar `karaoke.kar` e `completo.mid` em destaque; faixas individuais, relatórios, editor de letra e diagnóstico em seções recolhidas.
- "Abrir conversão salva" só aparece se houver conversões anteriores.

## Validação
131 testes automatizados passaram, incluindo novos casos para o nível do piano e para a compatibilidade com o antigo `mute_other`. A tela foi aberta com AppTest e em navegador. Não houve execução do iniciador Windows (.exe).
