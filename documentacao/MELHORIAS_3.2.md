# Versão 3.2 — filtro de notas fantasma do acompanhamento

O filtro agora é parte da conversão e atua sobre piano, teclado, guitarra e Other. Compara a intensidade de uma nota com as notas próximas no tempo, em vez de cortar toda nota abaixo de um volume fixo. Remove fragmentos fracos muito curtos e duplicatas fracas sobrepostas. Há níveis Suave, Equilibrado, Forte e Desativado; Equilibrado é o padrão.

## Correção harmônica

A opção "Corrigir notas fracas pela harmonia do trecho" usa:

- cifras com tempos explícitos ou associadas a palavras alinhadas ao áudio;
- tonalidade escolhida manualmente ou uma escala inferida com confiança suficiente;
- apoio das notas fortes próximas e da melodia vocal no mesmo instante.

Uma nota C da voz não define, sozinha, a tonalidade da música nem autoriza transformar todo acompanhamento em C maior. A voz só acrescenta contexto quando já há informação harmônica. Quando a análise tonal é ambígua, o programa não escolhe uma escala global arbitrariamente. Sem tempos confiáveis, cifras sem marcação temporal não são usadas para afinar pela distribuição uniforme da letra.

A correção desloca somente notas fracas, no máximo um semitom, e precisa de uma escolha inequívoca. Cifras locais preservam acordes emprestados; notas fortes e passagens válidas na escala não são forçadas a virar notas do acorde. A correção global anterior do perfil de referência foi retirada para evitar alterar alturas antes dessa análise contextual.

Voz, baixo e bateria não são alterados por este filtro. O andamento, os canais, controles e metadados continuam nos mesmos tempos absolutos. O filtro é aplicado às faixas individuais e ao MIDI combinado, antes da gravação da letra no KAR. Isso também funciona quando as notas iniciais vêm do cache.

## Onde conferir

Os resultados incluem `relatorio_filtro_acompanhamento.json`, com notas removidas/corrigidas e motivos. A pasta `midi_antes_filtro` preserva os MIDIs anteriores ao filtro. Para aplicar a uma conversão antiga, processe novamente; o cache pode evitar repetir etapas caras.

## Validação

125 testes passaram. Há casos de notas fracas e fortes, trecho uniformemente suave, acordes emprestados, nota vocal sem tonalidade, ambiguidade entre duas correções, mudanças de BPM, preservação de metadados e filtragem consistente entre arquivo individual e combinado. A interface e a escolha manual de C maior foram verificadas com AppTest.

No arquivo `other.mid` enviado: 3.340 notas antes, 2.541 depois; 799 notas suspeitas removidas. Os metadados, resolução, canais e tempos foram preservados. Como a tonalidade ficou ambígua, nenhuma altura foi modificada nessa cópia. Sem o áudio original correspondente, não é possível afirmar que toda nota removida era falsa, nem garantir que não restem notas falsas. Não houve teste nativo do iniciador Windows ou confirmação auditiva da fidelidade.
