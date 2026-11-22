"""Compact keyboard-first GTK4 instrument surface."""
from concurrent.futures import ThreadPoolExecutor
import gi

gi.require_foreign('cairo')
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Gtk
from .theme import css, palette
from .telemetry import Monitor, memory_text, HISTORY
from .diagnostics import Diagnostics
from .dots import draw


class Application(Gtk.Application):
    def __init__(self, controller):
        super().__init__(application_id='local.retrotools.RetroControl')
        self.control = controller
        self.selected = next(iter(controller.tools), None)
        self.busy = False
        self.launch_dialog = None
        self.launch_values = {}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.colors = None
        self.diagnostics = Diagnostics(controller.root)
        self.monitor = Monitor(controller.tools, self.diagnostics.emit)
        self.show_history = True
        self.connect('activate', self.activate)
        self.connect('shutdown', self.shutdown)

    def label(self, text='', style=None):
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        if style:
            label.add_css_class(style)
        return label

    def section(self, title):
        box = Gtk.Box(spacing=6)
        box.append(self.label('┌─', 'dim'))
        box.append(self.label(title, 'section'))
        line = Gtk.Separator()
        line.set_hexpand(True)
        line.set_valign(Gtk.Align.CENTER)
        box.append(line)
        return box

    def update_shortcuts(self):
        color = (self.colors or palette())['secondary']
        self.help.set_markup('   '.join(f'<span foreground="{color}">{key}</span> {name}' for key, name in
                             [('↑ ↓', 'SELECT'), ('S', 'START'), ('X', 'STOP'), ('R', 'RESTART'),
                              ('O', 'OPEN'), ('L', 'EVENTS'), ('?', 'HELP'), ('Q', 'QUIT')]))

    def activate(self, *_):
        if getattr(self, 'window', None):
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self, title='RetroControl')
        self.window.set_default_size(760, 550)
        self.window.add_css_class('retrocontrol')
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(outer, f'set_margin_{edge}')(10)
        self.window.set_child(outer)
        header = Gtk.Box(spacing=12)
        title = self.label('RETROCONTROL / 03.1', 'heading')
        title.set_hexpand(True)
        header.append(title)
        self.system = self.label('SYS: NOMINAL')
        header.append(self.system)
        outer.append(header)
        outer.append(Gtk.Separator())
        self.body = Gtk.Box(spacing=8)
        self.body.set_vexpand(True)
        outer.append(self.body)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.rows = {}
        self.row_labels = {}
        self.row_status = {}
        self.row_attention = {}
        for key, tool in self.control.tools.items():
            row = Gtk.ListBoxRow()
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            label = self.label(tool.name.upper(), 'tool-name')
            status_label = self.label('', 'tool-state')
            labels.append(label)
            status_line = Gtk.Box(spacing=8)
            status_line.append(status_label)
            marker = self.label('·', 'attention-idle')
            marker.set_tooltip_text('No unacknowledged telemetry peak')
            status_line.append(marker)
            self.row_attention[key] = marker
            labels.append(status_line)
            row.set_child(labels)
            self.list.append(row)
            self.rows[row] = key
            self.row_labels[key] = label
            self.row_status[key] = status_label
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sidebar = sidebar
        sidebar.add_css_class('bay')
        sidebar.append(self.section('TOOLS'))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.list)
        scroll.set_vexpand(True)
        sidebar.append(scroll)
        self.body.append(sidebar)
        self.list.connect('row-selected', self.select)
        clicks = Gtk.GestureClick()
        clicks.connect('released', lambda *_: self.acknowledge())
        self.list.add_controller(clicks)
        self.list.connect('row-activated', lambda *_: self.acknowledge())
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail.set_hexpand(True)
        detail.add_css_class('bay')
        self.selector = Gtk.DropDown(model=Gtk.StringList.new([t.name for t in self.control.tools.values()]))
        self.selector.set_visible(False)
        self.selector_handler = self.selector.connect('notify::selected', self.compact_select)
        detail.append(self.selector)
        self.name = self.label('', 'heading')
        self.telemetry = self.label()
        self.description = self.label('', 'dim')
        detail.append(self.name)
        detail.append(Gtk.Separator())
        self.state_heading = self.section('PROCESS / STATE')
        detail.append(self.state_heading)
        detail.append(self.telemetry)
        self.usage = Gtk.Grid(column_spacing=12, row_spacing=2)
        self.usage_labels = {}
        self.graphs = {}
        self.graph_data = {}
        for index, metric in enumerate(('cpu', 'mem', 'proc')):
            value = self.label('', 'running')
            value.set_wrap(False)
            self.usage_labels[metric] = value
            self.usage.attach(value, 0, index, 1, 1)
            if metric != 'proc':
                area = Gtk.DrawingArea()
                area.set_content_width(180)
                area.set_content_height(32)
                area.set_hexpand(True)
                area.set_draw_func(lambda area, cr, width, height, m:
                                   draw(cr, self.graph_data.get(m, ()), m, width, height,
                                        self.colors or palette(), HISTORY), metric)
                area.set_tooltip_text('CPU: logarithmic, at least 100% range. MEM: local range, at least 64 MiB. Last 3 minutes.')
                self.graphs[metric] = area
                self.usage.attach(area, 1, index, 1, 1)
        self.usage.set_tooltip_text('Verified root + current same-user descendants; reparented processes excluded. CPU: 100% = one core. MEM: RSS.')
        detail.append(self.usage)
        detail.append(self.description)
        actions = Gtk.FlowBox()
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.set_max_children_per_line(4)
        actions.set_min_children_per_line(1)
        self.buttons = {}
        for action in ('start', 'stop', 'restart', 'open'):
            button = Gtk.Button()
            caption = Gtk.Box(spacing=6)
            caption.append(self.label({'start': 'S', 'stop': 'X', 'restart': 'R', 'open': 'O'}[action], 'shortcut'))
            caption.append(self.label(action.upper()))
            button.set_child(caption)
            button.connect('clicked', lambda _, a=action: self.action(a))
            self.buttons[action] = button
            actions.insert(button, -1)
        self.actions_heading = self.section('CONTROL')
        detail.append(Gtk.Separator())
        detail.append(self.actions_heading)
        detail.append(actions)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        detail.append(spacer)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_hexpand(True)
        detail_scroll.set_vexpand(True)
        detail_scroll.set_child(detail)
        self.body.append(detail_scroll)
        self.event_bay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.event_bay.add_css_class('bay')
        self.event_bay.append(self.section('EVENTS / LAST 100'))
        self.events = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.events.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        console = Gtk.ScrolledWindow()
        console.set_min_content_height(110)
        console.set_child(self.events)
        self.event_bay.append(console)
        outer.append(self.event_bay)
        self.help = self.label('↑ ↓ SELECT   S START   X STOP   R RESTART   O OPEN   L EVENTS   ? HELP   Q QUIT', 'dim')
        outer.append(self.help)
        self.update_shortcuts()
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.key)
        self.window.add_controller(keys)
        if self.rows:
            self.list.select_row(next(iter(self.rows)))
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.timer = GLib.timeout_add(1000, self.tick)
        self.tick()
        self.window.present()

    def select(self, _, row):
        if row:
            self.selected = self.rows[row]
            self.monitor.acknowledge(self.selected)
            if hasattr(self, 'selector') and self.selector.get_selected() != row.get_index():
                self.selector.set_selected(row.get_index())
            self.render()

    def acknowledge(self):
        if self.selected:
            self.monitor.acknowledge(self.selected)
            self.render()

    def compact_select(self, selector, _):
        row = self.list.get_row_at_index(selector.get_selected())
        if row:
            self.list.select_row(row)

    def key(self, _, keyval, keycode, state):
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        key = (Gdk.keyval_name(keyval) or '').lower()
        if key in ('q', 'escape'):
            self.window.close()
        elif key in ('s', 'x', 'r', 'o'):
            self.action({'s': 'start', 'x': 'stop', 'r': 'restart', 'o': 'open'}[key])
        elif key in ('up', 'down'):
            row = self.list.get_selected_row()
            index = row.get_index() if row else 0
            index = max(0, min(len(self.rows) - 1, index + (1 if key == 'down' else -1)))
            self.list.select_row(self.list.get_row_at_index(index))
            self.acknowledge()
        elif key == 'l':
            self.event_bay.set_visible(True)
            self.events.grab_focus()
        elif key in ('h', 'question'):
            self.control.event('CONTROL', 'S start / X stop / R restart / O open / arrows select / Tab focus / Q quit. Tools survive Quit.')
            self.render()
        else:
            return False
        return True

    def action(self, action):
        if self.launch_dialog:
            self.launch_dialog.present()
            return
        if self.busy or not self.selected or not self.buttons[action].get_sensitive():
            return
        key = self.selected
        if action in ('start', 'restart') and self.control.tools[key].launch_parameters:
            self.show_launch(key, action)
        else:
            self.execute(action, key)

    def execute(self, action, key, parameters=None):
        self.busy = True
        self.render()
        arguments = (key, parameters) if action in ('start', 'restart') else (key,)
        future = self.pool.submit(getattr(self.control, action), *arguments)
        future.add_done_callback(lambda result: GLib.idle_add(self.finished, result))

    def show_launch(self, key, action):
        tool = self.control.tools[key]
        dialog = Gtk.Window(title=f'{tool.name} / {action.upper()}', transient_for=self.window,
                            modal=True, destroy_with_parent=True)
        self.launch_dialog = dialog
        dialog.add_css_class('retrocontrol')
        dialog.set_default_size(420, 240)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.add_css_class('bay')
        dialog.set_child(content)
        content.append(self.label(f'{tool.name.upper()} / {action.upper()}', 'heading'))
        content.append(Gtk.Separator())
        entries = {}
        values = self.launch_values.get(key, {})
        for parameter in tool.launch_parameters:
            content.append(self.label(parameter.label.upper(), 'section'))
            entry = Gtk.Entry()
            entry.set_text(values.get(parameter.id, parameter.default))
            entry.set_hexpand(True)
            entries[parameter.id] = entry
            content.append(entry)
        error = self.label('', 'error')
        error.set_visible(False)
        content.append(error)
        controls = Gtk.Box(spacing=8)
        controls.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label='CANCEL')
        engage = Gtk.Button(label=action.upper())
        controls.append(cancel)
        controls.append(engage)
        content.append(controls)

        def closed(*_):
            self.launch_dialog = None
            return False

        def submit(*_):
            parameters = {pid: entry.get_text() for pid, entry in entries.items()}
            try:
                tool.launch_command(parameters)
            except ValueError as exc:
                error.set_label(str(exc))
                error.set_visible(True)
                return
            self.launch_values[key] = parameters
            dialog.close()
            self.execute(action, key, parameters)

        def keyboard(_, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                dialog.close()
                return True
            # Entry.activate and the focused button handle Enter natively.
            return False

        cancel.connect('clicked', lambda _: dialog.close())
        engage.connect('clicked', submit)
        dialog.connect('close-request', closed)
        for entry in entries.values():
            entry.connect('activate', submit)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', keyboard)
        dialog.add_controller(keys)
        # Expose fields for the live UI regression test.
        dialog.launch_keys = keys
        dialog.launch_entries = entries
        dialog.launch_error = error
        dialog.present()
        first = next(iter(entries.values()))
        first.grab_focus()
        first.select_region(0, -1)

    def finished(self, future):
        try:
            future.result()
        except Exception as exc:
            self.control.event('CONTROL', f'Action error: {exc}')
        self.busy = False
        self.monitor.update(self.control.statuses)
        self.render()
        return False

    def tick(self):
        if not self.busy:
            self.control.refresh()
        self.monitor.update(dict(self.control.statuses))
        colors = palette()
        if colors != self.colors:
            self.provider.load_from_data(css(colors).encode())
            self.colors = colors
            self.update_shortcuts()
            for area in self.graphs.values():
                area.queue_draw()
        width, height = self.window.get_width(), self.window.get_height()
        self.adapt(width, height)
        self.render()
        return True

    def adapt(self, width, height):
        self.show_history = width >= 580 and height >= 420
        for area in self.graphs.values():
            area.set_visible(self.show_history)
        compact = (0 < width < 580 and 0 < height < 620) or 0 < height < 340
        self.sidebar.set_visible(not compact)
        self.selector.set_visible(compact)
        self.body.set_orientation(Gtk.Orientation.VERTICAL if 0 < width < 580 else Gtk.Orientation.HORIZONTAL)
        self.event_bay.set_visible(height == 0 or height >= 420)
        self.help.set_visible(height == 0 or height >= 340)
        self.state_heading.set_visible(height == 0 or height >= 340)
        self.actions_heading.set_visible(height == 0 or height >= 340)

    def render(self):
        if not hasattr(self, 'telemetry'):
            return
        faults = sum(s.state == 'ERROR' for s in self.control.statuses.values())
        self.system.set_label('SYS: BUSY' if self.busy else f'SYS: {faults} FAULT' if faults else 'SYS: NOMINAL')
        for key, label in self.row_labels.items():
            status = self.control.statuses[key]
            latched = self.monitor.attention[key].latched
            marker = self.row_attention[key]
            text = '!' if latched else '·'
            if marker.get_label() != text:
                marker.set_label(text)
                marker.remove_css_class('attention' if not latched else 'attention-idle')
                marker.add_css_class('attention' if latched else 'attention-idle')
                marker.set_tooltip_text('Telemetry peak — select to acknowledge' if latched else 'No unacknowledged telemetry peak')
            symbol = {'RUNNING': '●', 'STOPPED': '○', 'ERROR': '!'}[status.state]
            label.set_label(f'{symbol}  {self.control.tools[key].name.upper()}')
            state_label = self.row_status[key]
            state_label.set_label('MULTI / ' + status.state if status.instances > 1 else status.state)
            for style in ('running', 'stopped', 'error', 'ambiguous'):
                state_label.remove_css_class(style)
            state_label.add_css_class('ambiguous' if status.instances > 1 else status.state.lower())
        names = tuple(('! ' if self.monitor.attention[key].latched else '') + tool.name
                      for key, tool in self.control.tools.items())
        if getattr(self, 'selector_names', None) != names:
            model = self.selector.get_model()
            index = list(self.control.tools).index(self.selected) if self.selected else 0
            self.selector.handler_block(self.selector_handler)
            try:
                model.splice(0, model.get_n_items(), list(names))
                self.selector.set_selected(index)
            finally:
                self.selector.handler_unblock(self.selector_handler)
            self.selector_names = names
        if self.selected:
            tool = self.control.tools[self.selected]
            status = self.control.statuses[self.selected]
            self.name.set_label(tool.name.upper())
            sample = self.monitor.samples[self.selected]
            valid = (status.state == 'RUNNING' and status.instances == 1 and sample.key ==
                     (self.selected, tuple(status.identity[k] for k in ('pid', 'ticks', 'boot')))) if status.identity else False
            value = sample.value if valid else None
            cpu, mem, count = value if value else (None, None, None)
            texts = {'cpu': '—' if cpu is None else f'{cpu:.1f} %',
                     'mem': '—' if mem is None else memory_text(mem),
                     'proc': '—' if count is None else str(count)}
            for metric, label in self.usage_labels.items():
                text = f'{metric.upper():<10}{texts[metric]}'
                if label.get_label() != text:
                    label.set_label(text)
            for metric, values in (('cpu', sample.cpu), ('mem', sample.mem)):
                data = tuple(values) if valid and value else ()
                if self.graph_data.get(metric) != data:
                    self.graph_data[metric] = data
                    self.graphs[metric].queue_draw()
            seconds = status.elapsed
            duration = f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}' if seconds is not None else '—'
            self.telemetry.set_label(f'STATUS    {status.state}\nPID       {status.pid or "—"}\nUPTIME    {duration}\nEXIT      {status.exit_code if status.exit_code is not None else "—"}')
            for style in ('running', 'error'):
                self.telemetry.remove_css_class(style)
            if status.state == 'ERROR':
                self.telemetry.add_css_class('error')
            self.description.set_label(status.detail or '')
            self.description.set_visible(bool(status.detail))
            self.buttons['open'].set_tooltip_text('Open native tool control' if tool.supports_open else 'OPEN unsupported by this tool')
            running = status.state == 'RUNNING'
            for action, button in self.buttons.items():
                allowed = {'start': not running, 'stop': running, 'restart': True,
                           'open': running and tool.supports_open}[action]
                button.set_sensitive(not self.busy and status.instances <= 1 and allowed)
        else:
            self.name.set_label('NO TOOLS REGISTERED')
            for button in self.buttons.values():
                button.set_sensitive(False)
        text = '\n'.join(reversed(self.control.events))
        buffer = self.events.get_buffer()
        if getattr(self, 'event_text', None) != text:
            buffer.set_text(text)
            self.event_text = text

    def shutdown(self, *_):
        if hasattr(self, 'timer'):
            GLib.source_remove(self.timer)
        self.pool.shutdown(wait=True)
        self.monitor.close()
        self.diagnostics.close()
        self.control.close()
