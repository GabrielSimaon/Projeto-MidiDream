# Versão 3.7 — letra sincronizada pelo MP3

No modo somente letra, envie o MIDI, a letra e o MP3. O alinhador localiza o texto fornecido no canto e calcula os tempos por palavra; a saída MIDI/KAR contém eventos por sílaba e quebras de linha. Não recria os instrumentos. O MIDI original continua sendo necessário e deve corresponder à mesma versão do áudio.

O reconhecimento usa o processo isolado já existente (stable-ts + faster-whisper, modelo small em CPU). Inclui idioma português/inglês/espanhol e deslocamento em segundos para diferenças entre os inícios do MP3 e MIDI. A primeira execução pode baixar o modelo. Não aplica uma distribuição uniforme se o alinhamento falhar: mostra o erro e mantém o arquivo original intacto.

O modo manual permanece disponível sem áudio ou desmarcando a opção automática. O resultado fica disponível em MID e KAR; alterar MP3, texto, MIDI, idioma ou deslocamento invalida os botões do resultado anterior.

Validação: 143 testes automatizados aprovados. Testes da interface manual e automática, com decodificação de MP3 real e saída de alinhamento simulada. Verificados refrões repetidos com pausa instrumental, preservação de notas/controles/andamentos, deslocamentos, rejeição de tempos inválidos e invalidação de resultados antigos. A precisão do modelo em canto real não foi medida nesta alteração; os tempos continuam sendo estimativas e podem precisar de ajuste.
