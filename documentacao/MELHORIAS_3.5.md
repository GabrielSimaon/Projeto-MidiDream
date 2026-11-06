# Versão 3.5 — acompanhamento preso às cifras e baixo/bumbo com pressão

## Piano e acompanhamento (embolado / desafinado)
Novo filtro **Seguir as cifras**, agora o padrão em "Filtro de notas fantasma". Depois de remover as notas fracas, ele usa as cifras da letra, já alinhadas ao canto, para reescrever as faixas de piano, teclado e violão:

1. Nota fora do acorde vira a nota do acorde mais próxima (até 2 semitons). Só sobrevive fora do acorde se for forte, longa e da escala.
2. Nota que atravessa a troca de acorde sem pertencer ao novo acorde é cortada na troca.
3. Nenhuma nota passa de 6 tempos, o que elimina sustentos "fantasma" de dezenas de segundos.
4. Notas abaixo de Dó3 sobem uma oitava, liberando a região grave para o baixo.
5. A mesma altura repetida ou sobreposta vira uma só nota.
6. No máximo 5 notas simultâneas por faixa; as mais fortes ficam.

Voz, baixo e bateria não são alterados. Sem tempos acústicos confiáveis para as cifras, o filtro cai para o nível Forte. O relatório `relatorio_filtro_acompanhamento.json` mostra, para cada arquivo, quantas notas foram ajustadas, cortadas, unidas ou removidas.

## Baixo e bumbo
Novo controle **Pressão do baixo e do bumbo (%)**, de 0 a 100, padrão 70. Notas fracas sobem até um piso (baixo 82, bumbo 112) e as intensidades ficam mais uniformes. 0% mantém os valores originais. Em resultados já gerados, use "Ajustar volumes (sem converter de novo)". Os arquivos de acompanhamento precisam ser gerados de novo para usar as cifras.

## Validação
Testes automatizados novos para o travamento nas cifras e para a pressão. Numa conversão real do usuário, com acordes estimados só para teste, as notas dentro do acorde subiram de 34% para 100%.
