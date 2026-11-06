# Versão 2.9

A interface exige BPM manual (30–300, sem valor preenchido automaticamente) e letra com cifras válidas entre colchetes. O botão de processamento fica desativado enquanto houver entradas obrigatórias ausentes ou inválidas. A mesma validação é repetida antes de criar um trabalho ou iniciar o processamento. O comando de linha também exige --bpm e --lyrics.

O BPM informado é enviado ao motor e prevalece sobre o MIDI de referência. Importar KAR/JSON não substitui a letra com cifras obrigatória. A API interna mantém o modo instrumental compatível; as duas entradas destinadas ao usuário ativam a validação obrigatória.

O acompanhamento MIDI adicional pelas cifras continua opcional. As cifras não alteram automaticamente as notas extraídas da flauta. Um BPM constante não representa todas as variações possíveis de uma gravação ao vivo.

Mantidos os módulos e ajustes da versão 2.8. Validação: 96 testes passaram. AppTest conferiu campo de BPM inicialmente vazio, bloqueio com letra sem cifras/cifra inválida e liberação com os dois campos válidos. Esta alteração de interface/validação não exigiu nova conversão do áudio; não houve teste nativo do executável Windows.
