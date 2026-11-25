"""Notificações interativas do Windows (toasts).

Os dois notifiers usam o AUMID padrão da `windows_toasts` (o do
**cmd.exe**), de propósito: não passar `notifierAUMID` ao
`InteractableWindowsToaster`.

Entre 25/09 e 25/11/2026 o app usou um AUMID próprio, adotado pra
investigar descartes fantasma de gravação. Nesse período os pop-ups
deixaram de aparecer sem erro nenhum: `show_toast` retorna normal e o log
do Windows marca a notificação como entregue, mas nada salta na tela. Em
teste ao vivo, nem registrar o AUMID em `HKCU\\Software\\Classes\\
AppUserModelId` resolveu (com o identificador antigo e com um novo),
enquanto o mesmo toast com o AUMID padrão apareceu e entregou o clique.

A identidade do cmd.exe é compartilhada com outros processos. A proteção
contra um clique indevido fica no callback de cada toast, que só age sobre
a gravação daquele pop-up e loga o argumento recebido. Antes de voltar a um
AUMID próprio, confirmar na tela que o pop-up aparece."""
