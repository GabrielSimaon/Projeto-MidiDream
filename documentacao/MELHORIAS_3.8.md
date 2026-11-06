# 3.8 — presença de instrumentos e separação por fonte (experimental)

O novo fluxo usa AST treinado no AudioSet para classificar janelas de dez segundos de acompanhamento. São decisões multilabel independentes: uma música pode ter vários instrumentos ou nenhum dos quatro candidatos. Evidência moderada precisa aparecer em duas janelas; um pico forte admite solos curtos. Os índices são escores do modelo, não probabilidades calibradas. Um instrumento ausente pode ser falsamente detectado, e um presente pode passar despercebido.

Somente os candidatos presentes são enviados ao AudioSep com descrições específicas de sanfona, violão, guitarra elétrica e piano. Isso é separação no áudio antes de gerar MIDI, e não apenas troca de timbre. Voz, baixo e bateria seguem o separador existente. O modo completo impede a combinação Compacto e não transforma o resíduo em uma pista MIDI. O resíduo WAV fica disponível para conferência; outros tipos de instrumento além dos quatro alvos não são separados individualmente por este fluxo.

As saídas usam inferência com sobreposição em CPU, preservam duração e silêncio inicial e não recebem normalização individual que amplifique vazamentos baixos. Há filtragem de saídas com energia residual insignificante. Cada instrumento tem programa General MIDI e canal próprio: piano 2, sanfona 5, violão 6 e guitarra elétrica 7 (índices começando em zero). A sanfona não recebe o travamento de acordes destinado ao piano/violão. As faixas tradicionais de baixo, voz, bateria e guitarra legada mantêm seus canais.

O baixo detectado sobe 12 semitons após as verificações de afinação e antes da gravação; a mesma alteração aparece no arquivo individual e no combinado, sem somar oitavas na regravação rítmica. O cache MIDI mudou de versão para não reutilizar resultados anteriores à transposição.

O motor adicional é preparado automaticamente em ambiente Python próprio com acesso aos pacotes de áudio do aplicativo. Não substitui as bibliotecas de Whisper/Basic Pitch do ambiente principal. Os checkpoints AudioSep somam cerca de 3,6 GB, além do detector e dos componentes de texto; downloads são retomáveis e conferidos por SHA-256. O aplicativo exibe andamento e mantém os modelos e separações em cache. CPU e pouca RAM podem limitar a execução. A instalação principal também fixa versões de PyArrow e scikit-learn compatíveis com o NumPy 1.26 do motor de áudio.

## Validação

149 testes aprovados: decisões sem instrumentos presentes, presença simultânea, evidência incerta, canais/timbres, sobreposição temporal sem deslocamentos, cache sem nova inferência e baixo +12 em arquivo individual/completo. Interface de letra manual e automática conferida. Importações de todo o backend AudioSep/AST conferidas com dependências instaladas. Os pesos completos e a qualidade auditiva de separação de uma música real não foram validados nesta entrega. O novo modo é experimental.

## Fontes e licenças

- AudioSep: https://github.com/Audio-AGI/AudioSep — revisão 944583f18b84589dc965de3ad77525c945334252, licença MIT em programa/vendor/audiosep/LICENSE.
- Checkpoints oficiais: https://huggingface.co/spaces/Audio-AGI/AudioSep/tree/main/checkpoint
- Detector: https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593 — licença BSD-3-Clause.
- Alterações locais no código AudioSep: importação de pytorch_lightning, inicialização tardia do encoder padrão e do tokenizer de treinamento, para evitar carregar modelos durante a simples importação. Nenhum checkpoint é incluído no ZIP.
