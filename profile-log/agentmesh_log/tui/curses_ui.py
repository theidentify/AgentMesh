"""Optional curses front end; all input and rendering work with a fake screen."""
try:
    import curses
except ImportError:
    curses=None


def available(): return curses is not None


def start(controller):
    if not available(): raise ValueError('curses_unavailable')
    curses.wrapper(lambda screen: run(screen,controller,native=True))


def run(screen,controller,*,native=False):
    position=0; status=''; mode='read'
    def dialog(prompt):
        height,width=screen.getmaxyx()
        screen.addstr(height-1,0,prompt[:max(1,width-1)]); screen.refresh()
        if native: curses.echo()
        try:
            raw=screen.getstr(height-1,min(len(prompt),width-2),max(1,width-len(prompt)-1))
            return raw.decode('utf-8') if isinstance(raw,bytes) else raw
        finally:
            if native: curses.noecho()
    def terminal(fn):
        if native: curses.def_prog_mode(); curses.endwin()
        try: return fn()
        finally:
            if native: curses.reset_prog_mode(); screen.refresh()
    while True:
        model=controller.model; screen.erase(); height,width=screen.getmaxyx()
        lines=model.lines(); visible=max(1,height-4)
        offset=max(0,position-visible+2)
        header='1 Overview 2 Grid 3 Mapping 4 Pair | arrows move space tick r/w mode n scope a agent i request p approve s save q quit'
        screen.addstr(0,0,header[:max(1,width-1)])
        for row,text in enumerate(lines[offset:offset+visible],1):
            prefix='> ' if row+offset-1==position else '  '
            screen.addstr(row,0,(prefix+text)[:max(1,width-1)])
        screen.addstr(height-2,0,('mode='+mode+' '+status)[:max(1,width-1)]); screen.refresh()
        key=screen.getch()
        if key==ord('q'): return
        if key in (ord('1'),ord('2'),ord('3'),ord('4')): model.screen=key-ord('1'); position=0; status=''; continue
        if key in (259,ord('k')): position=max(0,position-1); continue
        if key in (258,ord('j')): position=min(len(lines)-1,position+1); continue
        if key in (260,ord('r')): mode='read'; continue
        if key in (261,ord('w')): mode='write'; continue
        try:
            if key==ord('s'):
                result=terminal(controller.save); status='Applied' if result.get('ok') else 'Apply refused: '+result.get('error','unavailable')
                position=0
            elif key==ord('n'): model.new_scope(dialog('Scope: '),dialog('Label: '))
            elif key==ord('a'): model.new_agent(dialog('Agent name: '))
            elif model.screen==1 and key==ord(' ') and position>0:
                cells=[(agent,scope) for agent in model.agents() for scope in model.scopes]
                if position-1<len(cells): model.toggle(*cells[position-1],mode)
            elif model.screen==2 and key in (10,13,ord(' ')) and 0<position<=len(model.projects):
                project=model.projects[position-1]['project']
                model.set_target(project,dialog('Target scope or skip: '))
            elif model.screen==3:
                if key==ord('i'): model.request_id=dialog('Request ID: ')
                elif key==ord('p'): terminal(controller.pair); status='Pair approved'
                elif key==ord(' ') and 3<=position<3+len(model.scopes):
                    scope=model.scopes[position-3]
                    if scope in model.pair_scopes: model.pair_scopes.remove(scope)
                    else: model.pair_scopes.add(scope)
        except (ValueError,EOFError): status='Operation refused; check names, scopes and presence.'
