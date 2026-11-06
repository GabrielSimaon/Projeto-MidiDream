# Versão 2.8

- Arranjo rítmico ativado por padrão na interface: baixo monofônico nos ataques de bumbo detectados; sem bateria, segue a grade. Só usa alturas que já estavam detectadas junto ao ataque. O violão usa colcheias, no máximo quatro notas simultâneas e une reataques sobrepostos. É uma simplificação do arranjo; pode eliminar contratempos e ornamentos corretos. Pode ser desativada.
- Flauta monofônica com pYIN sobre o stem vocal, detecção de regiões vozeadas, suavização e rejeição de notas muito curtas. Os tempos do canto não são quantizados. A escolha de timbre na interface prevalece sobre o timbre da referência.
- Letras digitadas ou importadas podem ser realinhadas ao áudio com stable-ts/faster-whisper. Isso substitui os tempos importados quando a opção está marcada. O alinhamento anterior distribuía palavras de refrões repetidos por intervalos grandes e comprimia outras no final da música. O novo alinhador usa o áudio. Pausas instrumentais não são atribuídas integralmente à primeira palavra seguinte.
- Linhas de letra menores para o visor de karaokê. Eventos FF05 em Latin-1, quebras CRLF e ausência de barras visíveis preservados. As sílabas dentro de cada palavra ainda são interpoladas; não há promessa de alinhamento fonético perfeito.
- Cache de instrumentos atualizado; alinhamento acústico possui cache próprio.

## Validação

91 testes automatizados, incluindo extração de notas em áudio sintético, pausas, monofonia, grade, acordes, realinhamento de letra importada e falha ao gravar relatórios. Interface conferida por AppTest.

Conversão integral de Pós a partir dos quatro stems do MP3 enviado, usando o próprio pipeline:

- Baixo: 204 notas, todas nos ataques de bumbo; uma nota por vez.
- Violão: 470 notas, grade de colcheias, até quatro alturas simultâneas.
- Flauta: 356 notas; uma nota por vez.
- Bateria: 529 notas.
- 452 eventos de letra. Verificadas as duas sequências de refrão: sem os intervalos anteriores de 9–13 segundos entre sílabas nesses trechos.
- MIDI Tipo 1, PPQ 960, aproximadamente 158 BPM, canais dedicados e pares Note On/Off consistentes.
- Teste integral de aproximadamente 49 segundos neste ambiente, com os stems e modelos já disponíveis. Esse tempo não inclui separação inicial ou download e não é previsão para outro computador.

O teste foi em Linux/CPU, com ONNX no Basic Pitch. O aplicativo Windows usa seu ambiente privado Python 3.11/TensorFlow já previsto no instalador. Não houve execução nativa do iniciador Windows nem confirmação auditiva da fidelidade. O modelo de melodia pode confundir coros/plateia; acompanhe o resultado em gravações ao vivo.
