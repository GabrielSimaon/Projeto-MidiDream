# Versão 3.6

O baixo é conferido por pYIN em blocos, antes da quantização. Só corrige semitons e erros harmônicos com consenso e confiança altos; não impõe uma escala. O modo Musical agora também usa seleção monofônica e suporte espectral no baixo. Voz e bateria mantêm os algoritmos existentes.

O piano descarta fragmentos curtos e fracos que destoam do contexto harmônico, incluindo o filtro Seguir as cifras. Notas longas continuam sujeitas às regras harmônicas anteriores. Não é garantia de transcrição perfeita, especialmente com vazamento entre instrumentos.

O modo Colocar somente letra em MIDI aceita MID/MIDI/KAR e texto/TXT. Permite definir início/fim do canto e revisar os tempos por linha. A distribuição inicial é aproximada e silábica; não identifica o canto sem áudio. Preserva as posições absolutas de notas, controles, programas, resolução e eventos de andamento. Substitui letras antigas mesmo em pistas mistas. MIDI tipo 2 é recusado porque possui sequências independentes.

YouTube: seleciona Deno/Node explicitamente, usa as ferramentas locais, atualiza yt-dlp e EJS em conjunto e repara Deno pelo botão de atualização no Windows. Uma única atualização e nova tentativa é feita em falhas técnicas (assinatura/403/formato). Confirmações de login/429 não geram repetição automática. As versões antigas fixas do baixador foram retiradas da instalação.

Referência técnica: https://github.com/yt-dlp/yt-dlp/wiki/EJS

Validação: testes sintéticos e regressão local. Não houve avaliação auditiva com stems desta música. O teste real reconheceu o vídeo e localizou o formato de áudio após usar os certificados do sistema, com validação TLS ativa. A rede deste ambiente retornou uma página de bloqueio no lugar do áudio; por isso o download completo no Windows ainda precisa ser confirmado.

Validação final: 140 testes automatizados aprovados e teste da interface com upload de MID, letra, geração e downloads MID/KAR.
