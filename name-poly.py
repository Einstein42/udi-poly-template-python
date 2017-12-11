#!/usr/bin/env python3
##!/home/e42/dev/py3_envs/udi-lifx-poly-venv/bin/python
"""
LiFX NodeServer for UDI Polyglot v2
by Einstein.42 (James Milne) milne.james@gmail.com
"""

import polyinterface as polyglot
import time
import sys
import lifxlan
from functools import wraps
from copy import deepcopy
import queue
import threading

LOGGER = polyglot.LOGGER
_SLOCK = threading.Lock()

# Changing these will not update the ISY names and labels, you will have to edit the profile.
COLORS = {
    0: ['RED', [62978, 65535, 65535, 3500]],
    1: ['ORANGE', [5525, 65535, 65535, 3500]],
    2: ['YELLOW', [7615, 65535, 65535, 3500]],
    3: ['GREEN', [16173, 65535, 65535, 3500]],
    4: ['CYAN', [29814, 65535, 65535, 3500]],
    5: ['BLUE', [43634, 65535, 65535, 3500]],
    6: ['PURPLE', [50486, 65535, 65535, 3500]],
    7: ['PINK', [58275, 65535, 47142, 3500]],
    8: ['WHITE', [58275, 0, 65535, 5500]],
    9: ['COLD_WHTE', [58275, 0, 65535, 9000]],
    10: ['WARM_WHITE', [58275, 0, 65535, 3200]],
    11: ['GOLD', [58275, 0, 65535, 2500]]
}

def socketLock(f):
    """
    Python Decorator to check global Socket Lock on LiFX mechanism. This prevents
    simultaneous use of the socket, which caused instability on the previous release.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        with _SLOCK:
            result = f(*args, **kwargs)
        return result
    return wrapper

class Control(polyglot.Controller):
    def __init__(self, poly):
        super().__init__(poly)
        self.lifxLan = lifxlan.LifxLAN(None)
        self.name = 'LiFX Control'
        self.address = 'lifxcontrol'
        self.primary = self.address
        self.discovery = False
        self.started = False
        self.q = queue.Queue()
        self.lifxThread = threading.Thread(target = self.processQueue)
        LOGGER.info('Started LiFX Protocol')

    def start(self):
        """
        Start polyinterface polls.
        """
        #self.startPolls()
        self.lifxThread.daemon = True
        self.lifxThread.start()
        self.discover()

    def processQueue(self):
        while True:
            cmd = self.q.get()
            with _SLOCK:
                cmd()

    def shortPoll(self, timer = 30):
        """
        Overridden shortPoll. It is imperative that you super this if you override it
        as the threading.Timer loop is in the parent method.
        """
        #super().shortPoll(timer)
        self.updateNodes()

    def discover(self, command = {}):
        if self.discovery == True: return
        self.discovery = True
        LOGGER.info('Starting LiFX Discovery...')
        try:
            with _SLOCK:
                devices = self.lifxLan.get_lights()
            LOGGER.info('{} bulbs found. Checking status and adding to ISY if necessary.'.format(len(devices)))
            for d in devices:
                with _SLOCK:
                    name = 'LIFX {}'.format(str(d.get_label()))
                    address = d.get_mac_addr().replace(':', '').lower()
                if not address in self.nodes:
                    with _SLOCK:
                        supportsMZ = d.supports_multizone()
                    if supportsMZ:
                        LOGGER.info('Found MultiZone Bulb: {}({})'.format(name, address))
                        self.nodes[address] = MultiZone(self, self.address, address, name, d)
                        self.addNode(self.nodes[address])
                        #time.sleep(.5)
                    else:
                        LOGGER.info('Found Bulb: {}({})'.format(name, address))
                        self.nodes[address] = Light(self, self.address, address, name, d)
                        self.addNode(self.nodes[address])
                        #time.sleep(.5)
                with _SLOCK:
                    gid, glabel, gupdatedat = d.get_group_tuple()
                gaddress = glabel.replace("'", "").replace(' ', '').lower()[:12]
                if not gaddress in self.nodes:
                    LOGGER.info('Found LiFX Group: {}'.format(glabel))
                    self.nodes[gaddress] = Group(self, self.address, gaddress, gid, glabel.replace("'", ""), gupdatedat)
                    self.addNode(self.nodes[gaddress])
                    #time.sleep(.5)
        except (lifxlan.WorkflowException, OSError, IOError, TypeError) as ex:
            LOGGER.error('discovery Error: {}'.format(ex))
        self.discovery = False

    def updateNodes(self):
        for node in self.nodes:
            self.nodes[node].updateInfo()

    drivers = []
    _commands = {'DISCOVER': discover}
    node_def_id = 'lifxcontrol'


class Light(polyglot.Node):
    """
    LiFX Light Parent Class
    """
    def __init__(self, parent, primary, address, name, device):
        super().__init__(parent, primary, address, name)
        self.device = device
        self.control = parent
        self.power = False
        self.parent = parent
        self.pending = False
        with _SLOCK:
            self.label = self.device.get_label()
        self.connected = True
        self.tries = 0
        self.uptime = 0
        self.color= []
        self.lastupdate = 0
        self.duration = 0

    def start(self):
        self.query()

    def nanosec_to_hours(self, ns):
        return round(ns/(1000000000.0*60*60), 2)

    def setOn(self, *args, **kwargs):
        try:
            self.parent.q.put(lambda: self.device.set_power(True))
            self.setDriver('ST', 1)
        except (lifxlan.WorkflowException): pass

    def setOff(self, *args, **kwargs):
        try:
            self.parent.q.put(lambda: self.device.set_power(False))
            self.setDriver('ST', 0)
        except (lifxlan.WorkflowException): pass

    def query(self, command = None):
        self.updateInfo()
        #self.reportDrivers()

    def setColor(self, command):
        if self.connected:
            _color = int(command.get('value'))
            try:
                self.parent.q.put(lambda: self.device.set_color(COLORS[_color][1], duration=self.duration, rapid=True))
            except (lifxlan.WorkflowException, IOError): pass
            LOGGER.info('Received SetColor command from ISY. Changing color to: {}'.format(COLORS[_color][0]))
            for ind, driver in enumerate(('GV1', 'GV2', 'GV3', 'CLITEMP')):
                self.setDriver(driver, COLORS[_color][1][ind])
        else: LOGGER.error('Received SetColor, however the bulb is in a disconnected state... ignoring')

    def setManual(self, command):
        if self.connected:
            _cmd = command.get('cmd')
            _val = int(command.get('value'))
            if _cmd == 'SETH':
                self.color[0] = _val
                driver = ['GV1', self.color[0]]
            elif _cmd == 'SETS':
                self.color[1] = _val
                driver = ['GV2', self.color[1]]
            elif _cmd == 'SETB':
                self.color[2] = _val
                driver = ['GV3', self.color[2]]
            elif _cmd == 'SETK':
                self.color[3] = _val
                driver = ['CLITEMP', self.color[3]]
            elif _cmd == 'SETD':
                self.duration = _val
                driver = ['RR', self.duration]
            try:
                self.parent.q.put(lambda: self.device.set_color(self.color, self.duration, rapid=True))
            except (lifxlan.WorkflowException, IOError): pass
            LOGGER.info('Received manual change, updating the bulb to: {} duration: {}'.format(str(self.color), self.duration))
            if driver:
                self.setDriver(driver[0], driver[1])
        else: self.logger.info('Received manual change, however the bulb is in a disconnected state... ignoring')

    def setHSBKD(self, command):
        query = command.get('query')
        try:
            self.color = [int(query.get('H.uom56')), int(query.get('S.uom56')), int(query.get('B.uom56')), int(query.get('K.uom26'))]
            self.duration = int(query.get('D.uom42'))
            LOGGER.info('Received manual change, updating the bulb to: {} duration: {}'.format(str(self.color), self.duration))
        except TypeError:
            self.duration = 0
        try:
            self.parent.q.put(lambda: self.device.set_color(self.color, duration=self.duration, rapid=False))
        except (lifxlan.WorkflowException, IOError): pass
        for ind, driver in enumerate(('GV1', 'GV2', 'GV3', 'CLITEMP')):
            self.setDriver(driver, self.color[ind])
        self.setDriver('RR', self.duration)

    def updateInfo(self):
        try:
            with _SLOCK:
                self.power = 1 if self.device.get_power() == 65535 else 0
                self.color = list(self.device.get_color())
                self.uptime = self.nanosec_to_hours(self.device.get_uptime())
            for ind, driver in enumerate(('GV1', 'GV2', 'GV3', 'CLITEMP')):
                self.setDriver(driver, self.color[ind])
            self.setDriver('ST', self.power)
            self.connected = 1
            self.tries = 0
        except (lifxlan.WorkflowException, OSError) as ex:
            if time.time() - self.lastupdate >= 60:
                LOGGER.error('During Query, device {} wasn\'t found. Marking as offline'.format(self.name))
                self.connected = 0
                self.uptime = 0
            else:
                LOGGER.error('Connection Error on color update_info. This happens from time to time, normally safe to ignore. %s', str(ex))
        else:
            self.setDriver('GV5', self.connected)
            self.setDriver('GV6', self.uptime)
            self.setDriver('RR', self.duration)
            self.lastupdate = time.time()

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25},
                {'driver': 'GV1', 'value': 0, 'uom': 56},
                {'driver': 'GV2', 'value': 0, 'uom': 56},
                {'driver': 'GV3', 'value': 0, 'uom': 56},
                {'driver': 'CLITEMP', 'value': 0, 'uom': 26},
                {'driver': 'GV5', 'value': 0, 'uom': 25},
                {'driver': 'GV6', 'value': 0, 'uom': 20},
                {'driver': 'RR', 'value': 0, 'uom': 42}]

    node_def_id = 'lifxcolor'

    _commands = {
                    'DON': setOn, 'DOF': setOff, 'QUERY': query,
                    'SET_COLOR': setColor, 'SETH': setManual,
                    'SETS': setManual, 'SETB': setManual,
                    'SETK': setManual, 'SETK': setManual,
                    'SETD': setManual, 'SET_HSBKD': setHSBKD
                }

class MultiZone(Light):
    def __init__(self, parent, primary, address, name, device):
        super().__init__(parent, primary, address, name, device)
        with _SLOCK:
            self.num_zones = len(self.device.get_color_zones())
        self.current_zone = 0
        self.new_color = None

    def updateInfo(self):
        try:
            with _SLOCK:
                self.power = 1 if self.device.get_power() == 65535 else 0
                if not self.pending:
                    self.color = self.device.get_color_zones()
                self.uptime = self.nanosec_to_hours(self.device.get_uptime())
            zone = deepcopy(self.current_zone)
            if self.current_zone != 0: zone -= 1
            for ind, driver in enumerate(('GV1', 'GV2', 'GV3', 'CLITEMP')):
                self.setDriver(driver, self.color[zone][ind])
            self.setDriver('ST', self.power)
            self.connected = 1
        except (lifxlan.WorkflowException, OSError, IOError, TypeError) as ex:
            if time.time() - self.lastupdate >= 60:
                LOGGER.error('During Query, device mz %s wasn\'t found for over 60 seconds. Marking as offline', self.name)
                self.connected = 0
                self.uptime = 0
                self.lastupdate = time.time()
            else:
                LOGGER.error('Connection Error on mz update_info. This happens from time to time, normally safe to ignore. %s', str(ex))
        else:
            self.setDriver('GV4', self.current_zone)
            self.setDriver('GV5', self.connected)
            self.setDriver('GV6', self.uptime)
            self.setDriver('RR', self.duration)
            self.lastupdate = time.time()

    def apply(self, command):
        try:
            if self.new_color:
                self.color = deepcopy(self.new_color)
                self.new_color = None
            self.parent.q.put(lambda: self.device.set_zone_colors(self.color, self.duration, rapid=True))
        except (lifxlan.WorkflowException, IOError): pass
        LOGGER.info('Received apply command for {}'.format(self.address))
        self.pending = False

    def setColor(self, command):
        if self.connected:
            try:
                _color = int(command.get('value'))
                zone = deepcopy(self.current_zone)
                if self.current_zone != 0: zone -= 1
                if self.current_zone == 0:
                    self.parent.q.put(lambda: self.device.set_zone_color(self.current_zone, self.num_zones, COLORS[_color][1], self.duration, True))
                else:
                    self.parent.q.put(lambda: self.device.set_zone_color(zone, zone, COLORS[_color][1], self.duration, True))
                LOGGER.info('Received SetColor command from ISY. Changing {} color to: {}'.format(self.address, COLORS[_color][0]))
            except (lifxlan.WorkflowException, IOError) as ex:
                LOGGER.error('mz setcolor error {}'.format(str(ex)))
            for ind, driver in enumerate(('GV1', 'GV2', 'GV3', 'CLITEMP')):
                self.setDriver(driver, COLORS[_color][1][ind])
        else: LOGGER.info('Received SetColor, however the bulb is in a disconnected state... ignoring')

    def setManual(self, command):
        if self.connected:
            _cmd = command.get('cmd')
            _val = int(command.get('value'))
            try:
                if _cmd == 'SETZ':
                    self.current_zone = int(_val)
                    if self.current_zone > self.num_zones: self.current_zone = 0
                    driver = ['GV4', self.current_zone]
                zone = deepcopy(self.current_zone)
                if self.current_zone != 0: zone -= 1
                new_color = list(self.color[zone])
                if _cmd == 'SETH':
                    new_color[0] = int(_val)
                    driver = ['GV1', new_color[0]]
                elif _cmd == 'SETS':
                    new_color[1] = int(_val)
                    driver = ['GV2', new_color[1]]
                elif _cmd == 'SETB':
                    new_color[2] = int(_val)
                    driver = ['GV3', new_color[2]]
                elif _cmd == 'SETK':
                    new_color[3] = int(_val)
                    driver = ['CLITEMP', new_color[3]]
                elif _cmd == 'SETD':
                    self.duration = _val
                    driver = ['RR', self.duration]
                self.color[zone] = new_color
                if self.current_zone == 0:
                    self.parent.q.put(lambda: self.device.set_zone_color(0, self.num_zones, new_color, self.duration, True))
                else:
                    self.parent.q.put(lambda: self.device.set_zone_color(zone, zone, new_color, self.duration, True))
            except (lifxlan.WorkflowException, TypeError) as ex:
                LOGGER.error('setmanual mz error {}'.format(ex))
            LOGGER.info('Received manual change, updating the mz bulb zone {} to: {} duration: {}'.format(zone, new_color, self.duration))
            if driver:
                self.setDriver(driver[0], driver[1])
        else: LOGGER.info('Received manual change, however the mz bulb is in a disconnected state... ignoring')

    def setHSBKDZ(self, command):
        query = command.get('query')
        if not self.pending:
            self.new_color = deepcopy(self.color)
            self.pending = True
        current_zone = int(query.get('Z.uom56'))
        zone = deepcopy(current_zone)
        if current_zone != 0: zone -= 1
        self.new_color[zone] = [int(query.get('H.uom56')), int(query.get('S.uom56')), int(query.get('B.uom56')), int(query.get('K.uom26'))]
        try:
            self.duration = int(query.get('D.uom42'))
        except TypeError:
            self.duration = 0
        try:
            if current_zone == 0:
                self.parent.q.put(lambda: self.device.set_zone_color(zone, self.num_zones, self.new_color, self.duration, True))
            else:
                self.parent.q.put(lambda: self.device.set_zone_color(zone, zone, self.new_color, self.duration, True, 0))
        except (lifxlan.WorkflowException, IOError) as ex:
            LOGGER.error('set mz hsbkdz error %s', str(ex))

    _commands = {
                    'DON': Light.setOn, 'DOF': Light.setOff,
                    'APPLY': apply, 'QUERY': Light.query,
                    'SET_COLOR': setColor, 'SETH': setManual,
                    'SETS': setManual, 'SETB': setManual,
                    'SETK': setManual, 'SETD': setManual,
                    'SETZ': setManual, 'SET_HSBKDZ': setHSBKDZ
                }

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25},
                {'driver': 'GV1', 'value': 0, 'uom': 56},
                {'driver': 'GV2', 'value': 0, 'uom': 56},
                {'driver': 'GV3', 'value': 0, 'uom': 56},
                {'driver': 'CLITEMP', 'value': 0, 'uom': 26},
                {'driver': 'GV4', 'value': 0, 'uom': 56},
                {'driver': 'GV5', 'value': 0, 'uom': 56},
                {'driver': 'GV6', 'value': 0, 'uom': 20},
                {'driver': 'RR', 'value': 0, 'uom': 42}]

    node_def_id = 'lifxmultizone'

class Group(polyglot.Node):
    """
    LiFX Group Node Class
    """
    def __init__(self, parent, primary, address, gid, label, gupdatedat):
        super().__init__(parent, primary, address, 'LIFX Group ' + str(label))
        self.group = gid
        self.label = label
        self.updated_at = gupdatedat
        self.members = []

    def start(self):
        self.query()
        #self.reportDrivers()

    def updateInfo(self):
        with _SLOCK:
            self.members = list(filter(lambda d: d.group == self.group, self.parent.lifxLan.get_lights()))
        self.setDriver('ST', len(self.members))

    def query(self, command = None):
        self.updateInfo()

    def setOn(self, command):
        LOGGER.info('Received SetOn command for group {} from ISY. Setting all {} members to ON.'.format(self.label, len(self.members)))
        for d in self.members:
            try:
                self.parent.q.put(lambda: d.set_power(True, rapid = True))
            except (lifxlan.WorkflowException, IOError) as ex:
                LOGGER.error('group seton error caught %s', str(ex))

    def setOff(self, command):
        LOGGER.info('Received SetOff command for group {} from ISY. Setting all {} members to OFF.'.format(self.label, len(self.members)))
        for d in self.members:
            try:
                self.parent.q.put(lambda: d.set_power(False, rapid = True))
            except (lifxlan.WorkflowException, IOError) as e:
                LOGGER.error('group setoff error caught {}'.format(str(e)))

    def setColor(self, command):
        _color = int(command.get('value'))
        for d in self.members:
            try:
                self.parent.q.put(lambda: d.set_color(COLORS[_color][1], 0, True))
            except (lifxlan.WorkflowException, IOError) as ex:
                LOGGER.error('group setcolor error caught %s', str(ex))
        LOGGER.info('Received SetColor command for group {} from ISY. Changing color to: {} for all {} members.'.format(self.name, COLORS[_color][0], len(self.members)))

    def setHSBKD(self, command):
        query = command.get('query')
        try:
            color = [int(query.get('H.uom56')), int(query.get('S.uom56')), int(query.get('B.uom56')), int(query.get('K.uom26'))]
            duration = int(query.get('D.uom42'))
        except TypeError:
            duration = 0
        for d in self.members:
            if d.supports_multizone():
                self.parent.q.put(lambda: d.set_zone_color(0, len(d.get_color_zones()), color, duration = duration, rapid = True))
            elif d.supports_color():
                self.parent.q.put(lambda: d.set_color(color, duration = duration, rapid = True))
        self.logger.info('Recieved SetHSBKD command for group {} from ISY, Setting all members to Color {}, duration {}'.format(self.label, color, duration))

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25}]

    _commands = {
                    'DON': setOn, 'DOF': setOff, 'QUERY': query,
                    'SET_COLOR': setColor, 'SET_HSBKD': setHSBKD
                }

    node_def_id = 'lifxgroup'

if __name__ == "__main__":
    try:
        """
        Grab the "LiFX" variable from the .polyglot/.env file. This is where
        we tell it what profile number this NodeServer is.
        """
        poly = polyglot.Interface("LiFX")
        poly.start()
        lifx = Control(poly)
        for thread in lifx._threads:
            thread.join()
        #while True:
        #time.sleep(.1)
        #if not _SLOCK:
        #input = poly.inQueue.get()
        #lifx.parseInput(input)
        #poly.inQueue.task_done()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
