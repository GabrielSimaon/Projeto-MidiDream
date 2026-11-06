# Versão 4.4 — MidiDream: máxima qualidade fixa e notas fiéis ao áudio

O programa passou a se chamar **MidiDream** (janela, executável, textos e documentação).
Pastas internas de instalação e variáveis `MIDI_STUDIO_*` foram mantidas de propósito,
para não obrigar um novo download de vários GB de modelos.

## Funções removidas da tela (inúteis ou que reduziam a qualidade)
| Função removida | Motivo |
|---|---|
| Modos Rápido, Equilibrado, Express | Rápido usava Demucs menor e cortava a análise; Express nem separava instrumentos |
| Escolha do modelo Demucs, "Separar com Demucs" | `htdemucs_6s` confundia piano e guitarra; agora é sempre `htdemucs_ft` |
| CPU/GPU | Escolhido automaticamente |
| "Testar só os primeiros 30 segundos", "Reaproveitar resultados" | Sem uso no fluxo final; o cache continua sempre ativo |
| "Notas em modo ultrarrápido" (YIN) | Menor precisão em voz e baixo |
| "Priorizar precisão da letra" e tamanho do Whisper | Sempre preciso, modelo `medium` |
| "Reconhecer a letra", "Alinhar a letra ao canto" | Sempre ligados |
| Arranjo compacto/detalhado, "Separar piano/violão", "Reconhecer sanfona…" | Fixos: reconhecimento completo com conferência de timbre |
| Filtro de notas fantasma (5 níveis) e limpeza de baixo/guitarra (4 níveis) | Fixos nos níveis recomendados; "Desativado" e "Forte" só pioravam o resultado |
| Escala obrigatória | Trocava notas reais pela "nota mais próxima da escala" |
| Baixo no bumbo e violão em colcheias | Apagava notas reais do baixo e do violão |
| Acompanhamento MIDI pelas cifras | Inventava notas que não existem na música |
| Grade/quantização dos instrumentos e PPQ | Fixos: tempos reais preservados, PPQ 960 |

## Por que havia notas inventadas e instrumentos trocados
1. A escala obrigatória movia toda nota "fora da escala" (voz, baixo, solos, notas de passagem).
2. O filtro "Escala e cifras" reescrevia o acompanhamento para os acordes digitados, subindo oitavas,
   cortando notas e limitando a 6 vozes.
3. O baixo era reduzido a uma nota por batida de bumbo e o violão a 4 notas por colcheia.
4. Limiares do Basic Pitch muito baixos (onset 0,20 / frame 0,12; o padrão do modelo é 0,5 / 0,3) e o
   "melodia trick" ligado acrescentavam notas que o modelo não tinha detectado.
5. Harmônicos (oitava, 12ª, 2 oitavas) de uma nota forte viravam notas novas.
6. A grade rítmica usava o BPM digitado com fase 0: um BPM arredondado ou uma música que não começa
   no segundo 0 deslocava a bateria para a subdivisão errada ao longo da música.
7. Modos rápidos usavam um Demucs menor, e o modelo de 6 faixas confundia piano com guitarra.

## O que mudou no motor
- `quality.py`: perfil fixo usado pela interface e pelo terminal.
- Escala, cifras, baixo no bumbo e quantização deixam de reescrever notas. O filtro continua só
  **removendo** notas fantasma fracas e curtas.
- Basic Pitch: limiares mais altos (piano 0,40/0,25; guitarra, acordeão e outros 0,35/0,22; baixo
  0,30/0,18), notas mínimas de 60 ms e sem "melodia trick".
- Novo filtro de harmônicos: remove nota fraca (≤ 60% da força) que começa junto de uma nota mais
  forte uma oitava, oitava+quinta ou duas oitavas abaixo. Oitavas tocadas com força parecida ficam.
- Grade rítmica calibrada no áudio: mede período (±4% do BPM digitado) e fase do pulso. O MIDI
  continua com o BPM digitado. Sem pulso claro, usa o BPM digitado como antes.
- Letra: alinhamento com stable-ts pulando pausas vocais longas e ajustando as bordas ao silêncio da
  faixa vocal (com retorno ao modo anterior se não rodar), Whisper `medium`, e tempo das sílabas
  proporcional ao tamanho de cada sílaba em vez de divisão igual.
- Cache de notas MIDI atualizado (versão 14): resultados antigos não são reaproveitados.

## Não alterado de propósito
- Baixo +12 semitons (decisão da versão 3.8, coberta por teste).
- BPM e letra com cifras continuam obrigatórios.
- Detecção de presença de instrumentos (limiares do AST): sem áudio real para medir, não foi mexida.

## Validação
- 180 testes automatizados passam (11 novos em `tests/test_fidelity_44.py`). Os 8 que falham também
  falhavam antes das mudanças: dependem de PyTorch e do banco de samples, ausentes no ambiente de teste.
- Grade rítmica testada com cliques sintéticos: período com erro menor que 0,01 BPM e fase com erro
  de poucos ms; ruído é rejeitado.
- **Não validado:** nenhum teste com música real, modelos Demucs/Basic Pitch/Whisper nem canto
  alinhado. Os limiares novos são conservadores e precisam ser conferidos no ouvido. O `MidiDream.exe`
  foi compilado em Linux (mingw) e não foi executado no Windows.
