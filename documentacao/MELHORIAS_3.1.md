# Versão 3.1 — cifras sem colchetes

Pode colar a cifra no formato comum, com acordes acima da letra:

```text
G4               G7M
Hoje eu voltei no passado
Em7              C9
E lembrei de nós dois
```

O programa associa a coluna de cada acorde à palavra mais próxima na linha seguinte. Essa associação é textual; o tempo musical continua dependendo do alinhamento com o áudio. Para um tempo explícito, o formato `[00:12.5 Am]` permanece disponível.

Cifras não ambíguas também podem aparecer entre palavras, como `G Hoje eu voltei D no passado`. Dentro de frases, A, E e Em são preservados como palavras; para esses acordes, prefira a linha de cifras acima da letra. O formato antigo `[G]Hoje` continua aceito.

## Acordes reconhecidos

- Maiores/menores e acidentes: G, Am, F#m, Bb.
- Suspensos: G4/Gsus4, G2/Gsus2, G7(4).
- Sétimas: G7, Gm7, G7M, GM7, Gmaj7 e G7+.
- Extensões: G6, G9, G11, G13, Gadd9, G6/9, G7M(9).
- Alterações: G7(b9), G7(#9), F#m7(b5), G7(#5).
- Diminutos/aumentados: Gdim, Gdim7, G°7, Gaug, G+.
- Inversões: C/G, D/F#.

O símbolo 4 significa sus4. Sétima maior usa 7M/M7/maj7. Extensões 9/11/13 incluem a sétima; add9 adiciona a nona à tríade. Acordes desconhecidos não são convertidos automaticamente para um acorde maior genérico.

Rótulos conhecidos, como `[Intro]`, `[Refrão]` e `[Segunda Parte]`, são removidos da letra cantada, inclusive quando aparecem antes de acordes na mesma linha. Espaços HTML e quebras de linha copiadas de texto formatado são normalizados.

BPM e letra com cifras continuam obrigatórios. Mantidos a faixa independente de chimbal, a grade própria da bateria, a flauta monofônica e os comentários do código.

Validação: 111 testes passaram, incluindo notas dos acordes, posição das cifras sobre a letra, formato antigo, caracteres especiais, títulos de partes e geração MIDI/KAR com cifras sem colchetes. Interface validada por AppTest. Não houve nova conversão integral do áudio nem execução nativa do iniciador Windows nesta alteração.
