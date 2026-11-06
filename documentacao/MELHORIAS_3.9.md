# 3.9 — acompanhamento preservado quando a identificação é incerta

A versão 3.8 combinava novamente piano e guitarra já separados e condicionava sua presença no MIDI a um segundo classificador. Candidatos não reconhecidos ficavam somente no WAV residual, fora da pasta de transcrição. Isso podia deixar o MIDI apenas com baixo, bateria e voz.

Agora as fontes de piano, guitarra/violão, sanfona, violão identificado e guitarra elétrica identificada são preservadas em faixas individuais. O segundo detector recebe somente as fontes ainda desconhecidas. Uma identificação incerta não elimina uma fonte já separada e não é apresentada como prova de ausência de um instrumento.

O residual com energia significativa também entra na transcrição em other.wav/other.mid, sinalizado como acompanhamento não identificado. Seu timbre é genérico: esse caminho preserva conteúdo, mas não substitui a identificação/separação correta. Pode haver vazamentos ou notas duplicadas no residual; ele deve ser revisado. Silêncio não cria uma faixa instrumental. Não houve redução cega dos limiares que aumentasse a criação de instrumentos específicos inexistentes.

Se o detector ou o modelo de extração adicional falharem, o relatório registra o erro e o acompanhamento disponível é conservado para transcrição. Resultados com erros não entram no cache de separação. Cache de separação versão 2 e cache MIDI versão 13 evitam reaproveitar os resultados incompletos da versão anterior.

O reconhecimento e a classificação de áudio continuam sendo estimativas. Esta atualização corrige o descarte causado pelo fluxo; não garante a precisão das notas transcritas nem o isolamento perfeito dos instrumentos.

Validação: 153 testes aprovados, incluindo novas regressões para piano/guitarra sem votos do detector, entrada do acompanhamento incerto na pasta transcrita, separação entre fontes conhecidas e desconhecidas, preservação do áudio do baixo e falhas do detector. Interfaces de letra manual e automática conferidas. A qualidade auditiva com a gravação específica do usuário não foi medida nesta alteração. Bateria e baixo usam o mesmo processamento da versão 3.8.
