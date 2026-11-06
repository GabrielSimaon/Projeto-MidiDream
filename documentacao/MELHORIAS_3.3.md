# Versão 3.3 — caixa, tons e surdo

## O que mudou

O detector passou a procurar ataques entre 80 e 500 Hz, incluindo tons graves que antes podiam virar bumbo. Depois do estalo inicial, analisa até 140 ms do corpo: concentração de energia na ressonância indica tom; ruído espalhado entre 500 e 4.500 Hz indica esteira de caixa. Mesmo esteira fraca é considerada, pois o demixing pode atenuá-la.

A decisão não depende apenas de uma frequência de corte: a caixa também tem corpo afinado. Quando um ataque é identificado como tom, ele deixa de gerar automaticamente uma caixa ou um bumbo duplicado. Subgrave suficientemente forte permite manter bumbo simultâneo. Vazamento agudo muito baixo de um tom não deve produzir chimbal falso.

Os tons/surdo usam notas GM 41, 43, 45, 47, 48 e 50, da região mais grave à mais aguda. A escolha vem da região de ressonância detectada, sem afinar a bateria na escala da música. Permanecem no canal 10 da bateria e no arquivo drums.mid. O chimbal continua em faixa própria.

## Como usar

Extraia o ZIP completo numa pasta nova, abra o executável e processe novamente a música. A interface tem **Tons / surdo (%)**, de 0 a 150%, tanto na conversão quanto nos ajustes dos resultados. 100% preserva a intensidade relativa detectada; 0% silencia essas notas sem deslocar o restante. Caixa, chimbal e pratos mantêm controles próprios.

O volume de um resultado antigo não recupera tons já transcritos como caixa: para isso, converta novamente. A versão do cache foi incrementada para não reaproveitar a classificação antiga. As viradas seguem o BPM e a grade da bateria já existentes.

## Código e validação

`drum_classifier.py` concentra a classificação comentada; `audio2midi_core.py` detecta os ataques e gera as notas; `midi_mix.py` aplica os controles independentes; `app.py` mostra o novo controle.

128 testes automatizados passaram. Os novos casos verificam seis regiões de tom, caixa com corpo ressonante e esteira atenuada, silêncio, bumbo, virada em áudio nos modos normal e rápido, ausência de caixa/bumbo/chimbal falsos nesses tons sintéticos, grade de semicolcheias e volume independente. Os testes existentes de crash e chimbal simultâneos continuam passando.

AppTest verificou o controle inicialmente em 100% e sua alteração para 125%. O stem completo disponível, com 174,66 s, também foi processado. Esse processamento confirma execução, não precisão auditiva por batida. Não houve execução nativa do iniciador Windows.

## Limites

É uma classificação heurística, sem novo modelo pesado. Afinações incomuns, esteira quase ausente, vazamento de outros instrumentos e caixa/tom sobrepostos podem continuar ambíguos. Os limiares são conservadores para reduzir caixas trocadas por tons; não há garantia de reconstrução exata de todas as peças.
