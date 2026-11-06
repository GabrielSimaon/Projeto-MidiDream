# Correções 4.1

- Botão de escolher pasta substituído por navegador dentro da interface, sem PowerShell ou janelas externas.
- Escolha do disco, navegação de subpastas, pasta anterior e confirmação de destino por callback.
- Pasta dos modelos/cache configurável e persistente; cache de conversão, Hugging Face, Torch, uv/pip e temporários seguem essa escolha.
- As pastas antigas não são movidas automaticamente e permanecem intactas.
- Verificação do espaço livre antes da conversão e antes de baixar checkpoints AudioSep.
- Gravação atômica de preferências sem apagar a configuração dos modelos ao mudar o destino.

O log recebido indicava somente 69,72 MB disponíveis no disco C: antes de baixar
um arquivo de 498,82 MB. A falha de gravação de áudio é compatível com a falta
de espaço. Escolher apenas a saída da música não redirecionava os modelos.

Validação: testes automatizados das preferências e do navegador de pastas;
AppTest clicando no seletor, abrindo subpastas, confirmando destino e alterando
a pasta dos modelos. Não executado em Windows nem repetida a transcrição completa.
