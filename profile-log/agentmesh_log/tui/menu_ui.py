"""Numbered terminal front end over the shared screen model."""

def run(controller, *, input_fn=input, output=print):
    def show(screen):
        for line in controller.model.lines(screen): output(line)
    while True:
        try:
            output('1 Overview  2 Agents x scopes  3 Mapping  4 Pair approve  5 Save and apply  0 Quit')
            action=input_fn('Choice: ')
            if action=='0': return
            if action=='1': show(0)
            elif action=='2':
                while True:
                    show(1); output('1 Toggle  2 New scope  3 New agent  0 Back')
                    edit=input_fn('Choice: '); model=controller.model
                    if edit=='0': break
                    if edit=='1': model.toggle(input_fn('Agent: '),input_fn('Scope: '),input_fn('read or write: '))
                    elif edit=='2': model.new_scope(input_fn('Scope path: '),input_fn('Label (general/internal/secret): '))
                    elif edit=='3': model.new_agent(input_fn('Agent name: '))
            elif action=='3':
                show(2)
                project=input_fn('Project name, @global or @null (blank to go back): ')
                if project: controller.model.set_target(project,input_fn('Target scope or skip: ')); show(2)
            elif action=='4':
                model=controller.model; model.request_id=input_fn('Request ID: ')
                while True:
                    show(3)
                    for i,scope in enumerate(model.scopes,1): output(str(i)+' Toggle '+scope)
                    output('a Approve (compare code)  0 Back'); choice=input_fn('Choice: ')
                    if choice=='0': break
                    if choice=='a': output(str(controller.pair())); break
                    if choice.isdecimal() and 1<=int(choice)<=len(model.scopes):
                        scope=model.scopes[int(choice)-1]
                        if scope in model.pair_scopes: model.pair_scopes.remove(scope)
                        else: model.pair_scopes.add(scope)
            elif action=='5':
                result=controller.save(); output('Saved and applied' if result.get('ok') else 'Apply refused: '+result.get('error','unavailable'))
        except (EOFError,KeyboardInterrupt): return
        except ValueError:
            output('Operation refused; check names, scopes and presence.')
