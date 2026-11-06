# Versão 3.0 — chimbal e grade da bateria

## O que mudou

O detector anterior classificava um ataque agudo como prato **ou** chimbal. Agora o crash é analisado pela cauda da banda média-aguda, enquanto o chimbal tem sua própria lista de ataques na banda aguda. As duas peças podem ser detectadas no mesmo instante. Isso não permite identificar com certeza todos os sons sobrepostos; ainda depende da evidência no áudio.

O MIDI combinado contém uma faixa `Drums` e outra `Drums - Chimbal`. O arquivo `hihat.mid` permite ouvir somente o chimbal. `drums.mid` continua contendo a bateria completa, incluindo a faixa de chimbal. Ambas as faixas usam o canal 10 de percussão General MIDI. A separação é de eventos/faixas MIDI, não uma nova separação de áudio por peça da bateria.

A bateria tem uma grade própria, independente da quantização dos instrumentos melódicos. O padrão é **16 — semicolcheias**, seguindo o BPM obrigatório informado pelo usuário. Também é possível escolher colcheias ou fusas na interface. Cada ataque vai à subdivisão mais próxima; não há compressão de uma virada inteira para encaixá-la em menos batidas. Isso pode simplificar rufos, tercinas e detalhes fora da grade escolhida.

Ataques duplicados da mesma peça na mesma posição são unidos, mantendo o mais forte. Notas consecutivas da mesma altura não se sobrepõem: o Note Off anterior não fica depois do próximo ataque. O cache de notas foi atualizado para não reutilizar uma bateria gerada pela versão anterior.

## Validação

- 101 testes passaram, incluindo crash e chimbal simultâneos, chimbal durante a cauda do crash, virada preservando a quantidade de batidas, remoção de duplicatas e exportação do arquivo de chimbal.
- Interface conferida com AppTest; BPM e letra com cifras continuam obrigatórios.
- Stem completo de bateria da música enviada: 174,66 segundos, 158 BPM, 278 notas de chimbal e 356 das demais peças. Todas as notas verificadas na grade; MIDI reaberto com duas faixas de percussão.
- Código organizado e comentado mantido. Não houve teste nativo do executável Windows nem avaliação auditiva da fidelidade musical.

Para aplicar aos resultados antigos, gere a música novamente. Os arquivos já salvos não são alterados automaticamente.
