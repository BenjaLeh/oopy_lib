#!/usr/bin/env python
#
#   Copyright 2015 Jonas Berg
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

#"""
#.. moduleauthor:: Jonas Berg <pyhys@users.sourceforge.net>
#MinimalModbus: A Python driver for the Modbus RTU and Modbus ASCII protocols via serial port (via RS485 or RS232).
#"""

#__author__   = 'Jonas Berg'
#__email__    = 'pyhys@users.sourceforge.net'
#__url__      = 'https://github.com/pyhys/minimalmodbus'
#__license__  = 'Apache License, Version 2.0'

#__version__  = '0.7'
#__status__   = 'Beta'


import os
from pyb import UART
import struct
import sys
import time

if sys.version > '3':
    import binascii

# Allow long also in Python3
# http://python3porting.com/noconv.html
if sys.version > '3':
    long = int

_NUMBER_OF_BYTES_PER_REGISTER = 2
_SECONDS_TO_MILLISECONDS = 1

# Several instrument instances can share the same serialport
_SERIALPORTS = {}
_LATEST_READ_TIMES = {}

####################
## Default values ##
####################

BAUDRATE = 9600
#"""Default value for the baudrate in Baud (int)."""

PARITY   = None
#"""Default value for the parity. See the pySerial module for documentation. Defaults to serial.PARITY_NONE"""

BYTESIZE = 8
#"""Default value for the bytesize (int)."""

STOPBITS = 1
#"""Default value for the number of stopbits (int)."""

TIMEOUT  = 1000
#"""Default value for the timeout value in seconds (float)."""

CLOSE_PORT_AFTER_EACH_CALL = False
#"""Default value for port closure setting."""

#####################
## Named constants ##
#####################

MODE_RTU   = 'rtu'

##############################
## Modbus instrument object ##
##############################


class Instrument():
#    """Instrument class for talking to instruments (slaves) via the Modbus RTU or ASCII protocols (via RS485 or RS232).

#    Args:
#        * port (str): The serial port name, for example ``/dev/ttyUSB0`` (Linux), ``/dev/tty.usbserial`` (OS X) or ``COM4`` (Windows).
#        * slaveaddress (int): Slave address in the range 1 to 247 (use decimal numbers, not hex).
#        * mode (str): Mode selection. Can be MODE_RTU or MODE_ASCII.

#    """

    def __init__(self, port, slaveaddress, mode=MODE_RTU, **kwargs):
        self.port = port
        self.stopbits   = kwargs.get('stopbits', STOPBITS)
        self.bytesize   = kwargs.get('bytesize', BYTESIZE)
        self.parity     = kwargs.get('parity', PARITY)
        self.baudrate   = kwargs.get('baudrate', BAUDRATE)
        self.timeout    = kwargs.get('timeout', TIMEOUT)

        self.serial = UART(self.port, self.baudrate)
        self.serial.init(self.baudrate, bits = self.bytesize, stop = self.stopbits, 
            timeout = self.timeout, parity = self.parity)
        self.address = slaveaddress

        self.mode = mode

        self.debug = False

        self.precalculate_read_size = True
       
        self.handle_local_echo = False

    def __repr__(self):
        return "{}.{}<id=0x{:x}, address={}, mode={}, close_port_after_each_call={}, precalculate_read_size={}, debug={}, serial={}>".format(
            self.__module__,
            self.__class__.__name__,
            id(self),
            self.address,
            self.mode,
            self.precalculate_read_size,
            self.debug,
            self.serial,
            )

    ######################################
    ## Methods for talking to the slave ##
    ######################################

    def write_register(self, registeraddress, value, numberOfDecimals=0, functioncode=6, signed=False):
        _checkFunctioncode(functioncode, [6, 16])
        _checkInt(numberOfDecimals, minvalue=0, maxvalue=10, description='number of decimals')
        _checkBool(signed, description='signed')
        _checkNumerical(value, description='input value')

        self._genericCommand(functioncode, registeraddress, value, numberOfDecimals, signed=signed)


    def read_registers(self, registeraddress, numberOfRegisters, functioncode=3):
        _checkFunctioncode(functioncode, [3, 4])
        _checkInt(numberOfRegisters, minvalue=1, description='number of registers')
        return self._genericCommand(functioncode, registeraddress, \
            numberOfRegisters=numberOfRegisters, payloadformat='registers')


    def write_registers(self, registeraddress, values):
        if not isinstance(values, list):
            raise TypeError('The "values parameter" must be a list. Given: {0!r}'.format(values))
        _checkInt(len(values), minvalue=1, description='length of input list')
        # Note: The content of the list is checked at content conversion.

        self._genericCommand(16, registeraddress, values, numberOfRegisters=len(values), payloadformat='registers')

    #####################
    ## Generic command ##
    #####################


    def _genericCommand(self, functioncode, registeraddress, value=None, \
            numberOfDecimals=0, numberOfRegisters=1, signed=False, payloadformat=None):
        NUMBER_OF_BITS = 1
        NUMBER_OF_BYTES_FOR_ONE_BIT = 1
        NUMBER_OF_BYTES_BEFORE_REGISTERDATA = 1
        ALL_ALLOWED_FUNCTIONCODES = list(range(1, 7)) + [15, 16]  # To comply with both Python2 and Python3
        MAX_NUMBER_OF_REGISTERS = 255

        # Payload format constants, so datatypes can be told apart.
        # Note that bit datatype not is included, because it uses other functioncodes.
        PAYLOADFORMAT_REGISTER  = 'register'
        PAYLOADFORMAT_REGISTERS = 'registers'

        ALL_PAYLOADFORMATS = [ PAYLOADFORMAT_REGISTER, PAYLOADFORMAT_REGISTERS]

        ## Check input values ##
        _checkFunctioncode(functioncode, ALL_ALLOWED_FUNCTIONCODES)  # Note: The calling facade functions should validate this
        _checkRegisteraddress(registeraddress)
        _checkInt(numberOfDecimals, minvalue=0, description='number of decimals')
        _checkInt(numberOfRegisters, minvalue=1, maxvalue=MAX_NUMBER_OF_REGISTERS, description='number of registers')
        _checkBool(signed, description='signed')

        if payloadformat is not None:
            if payloadformat not in ALL_PAYLOADFORMATS:
                raise ValueError('Wrong payload format variable. Given: {0!r}'.format(payloadformat))

        ## Check combinations of input parameters ##
        numberOfRegisterBytes = numberOfRegisters * _NUMBER_OF_BYTES_PER_REGISTER

                    # Payload format
        if functioncode in [3, 4, 6, 16] and payloadformat is None:
            payloadformat = PAYLOADFORMAT_REGISTER

        if functioncode in [3, 4, 6, 16]:
            if payloadformat not in ALL_PAYLOADFORMATS:
                raise ValueError('The payload format is unknown. Given format: {0!r}, functioncode: {1!r}.'.\
                    format(payloadformat, functioncode))
        else:
            if payloadformat is not None:
                raise ValueError('The payload format given is not allowed for this function code. ' + \
                    'Given format: {0!r}, functioncode: {1!r}.'.format(payloadformat, functioncode))

                    # Signed and numberOfDecimals
        if signed:
            if payloadformat not in [PAYLOADFORMAT_REGISTER, PAYLOADFORMAT_LONG]:
                raise ValueError('The "signed" parameter can not be used for this data format. ' + \
                    'Given format: {0!r}.'.format(payloadformat))

        if numberOfDecimals > 0 and payloadformat != PAYLOADFORMAT_REGISTER:
            raise ValueError('The "numberOfDecimals" parameter can not be used for this data format. ' + \
                'Given format: {0!r}.'.format(payloadformat))

                    # Number of registers
        if functioncode not in [3, 4, 16] and numberOfRegisters != 1:
            raise ValueError('The numberOfRegisters is not valid for this function code. ' + \
                'NumberOfRegisters: {0!r}, functioncode {1}.'.format(numberOfRegisters, functioncode))

        if functioncode == 16 and payloadformat == PAYLOADFORMAT_REGISTER and numberOfRegisters != 1:
            raise ValueError('Wrong numberOfRegisters when writing to a ' + \
                'single register. Given {0!r}.'.format(numberOfRegisters))
            # Note: For function code 16 there is checking also in the content conversion functions.

                    # Value
        if functioncode in [5, 6, 15, 16] and value is None:
            raise ValueError('The input value is not valid for this function code. ' + \
                'Given {0!r} and {1}.'.format(value, functioncode))

        if functioncode == 16 and payloadformat in [PAYLOADFORMAT_REGISTER]:
            _checkNumerical(value, description='input value')

        if functioncode == 6 and payloadformat == PAYLOADFORMAT_REGISTER:
            _checkNumerical(value, description='input value')

                    # Value for string
        if functioncode == 16 and payloadformat == PAYLOADFORMAT_REGISTERS:
            if not isinstance(value, list):
                raise TypeError('The value parameter must be a list. Given {0!r}.'.format(value))

            if len(value) != numberOfRegisters:
                raise ValueError('The list length does not match number of registers. ' + \
                    'List: {0!r},  Number of registers: {1!r}.'.format(value, numberOfRegisters))

        ## Build payload to slave ##
        if functioncode in [1, 2]:
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _numToTwoByteArray(NUMBER_OF_BITS)

        elif functioncode in [3, 4]:
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _numToTwoByteArray(numberOfRegisters)

        elif functioncode == 5:
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _createBitpattern(functioncode, value)

        elif functioncode == 6:
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _numToTwoByteArray(value, numberOfDecimals, signed=signed)

        elif functioncode == 15:
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _numToTwoByteArray(NUMBER_OF_BITS) + \
                            _numToOneByteArray(NUMBER_OF_BYTES_FOR_ONE_BIT) + \
                            _createBitpattern(functioncode, value)

        elif functioncode == 16:
            if payloadformat == PAYLOADFORMAT_REGISTER:
                registerdata = _numToTwoByteArray(value, numberOfDecimals, signed=signed)
            elif payloadformat == PAYLOADFORMAT_REGISTERS:
                registerdata = _valuelistToBytestring(value, numberOfRegisters)

            assert len(registerdata) == numberOfRegisterBytes
            payloadToSlave = _numToTwoByteArray(registeraddress) + \
                            _numToTwoByteArray(numberOfRegisters) + \
                            _numToOneByteArray(numberOfRegisterBytes) + \
                            registerdata

        ## Communicate ##
        payloadFromSlave = self._performCommand(functioncode, payloadToSlave)

        ## Check the contents in the response payload ##
        if functioncode in [1, 2, 3, 4]:
            _checkResponseByteCount(payloadFromSlave)  # response byte count

        if functioncode in [5, 6, 15, 16]:
            _checkResponseRegisterAddress(payloadFromSlave, registeraddress)  # response register address

        if functioncode == 5:
            _checkResponseWriteData(payloadFromSlave, _createBitpattern(functioncode, value))  # response write data

        if functioncode == 6:
            _checkResponseWriteData(payloadFromSlave, \
                _numToTwoByteArray(value, numberOfDecimals, signed=signed))  # response write data

        if functioncode == 15:
            _checkResponseNumberOfRegisters(payloadFromSlave, NUMBER_OF_BITS)  # response number of bits

        if functioncode == 16:
            _checkResponseNumberOfRegisters(payloadFromSlave, numberOfRegisters)  # response number of registers

        ## Calculate return value ##
        if functioncode in [1, 2]:
            registerdata = payloadFromSlave[NUMBER_OF_BYTES_BEFORE_REGISTERDATA:]
            if len(registerdata) != NUMBER_OF_BYTES_FOR_ONE_BIT:
                raise ValueError('The registerdata length does not match NUMBER_OF_BYTES_FOR_ONE_BIT. ' + \
                    'Given {0}.'.format(len(registerdata)))

            return _bitResponseToValue(registerdata)

        if functioncode in [3, 4]:
            registerdata = payloadFromSlave[NUMBER_OF_BYTES_BEFORE_REGISTERDATA:]
            if len(registerdata) != numberOfRegisterBytes:
                raise ValueError('The registerdata length does not match number of register bytes. ' + \
                    'Given {0!r} and {1!r}.'.format(len(registerdata), numberOfRegisterBytes))

            elif payloadformat == PAYLOADFORMAT_REGISTERS:
                return _bytearrayToValuelist(registerdata, numberOfRegisters)

            elif payloadformat == PAYLOADFORMAT_REGISTER:
                return _twoByteStringToNum(registerdata, numberOfDecimals, signed=signed)

            raise ValueError('Wrong payloadformat for return value generation. ' + \
                'Given {0}'.format(payloadformat))

    ##########################################
    ## Communication implementation details ##
    ##########################################


    def _performCommand(self, functioncode, payloadToSlave):
        DEFAULT_NUMBER_OF_BYTES_TO_READ = 1000

        _checkFunctioncode(functioncode, None)
        _checkString(payloadToSlave, description='payload')

        # Build request
        request = _embedPayload(self.address, self.mode, functioncode, payloadToSlave)

        # Calculate number of bytes to read
        number_of_bytes_to_read = DEFAULT_NUMBER_OF_BYTES_TO_READ
        if self.precalculate_read_size:
            try:
                number_of_bytes_to_read = _predictResponseSize(self.mode, functioncode, payloadToSlave)
            except:
                if self.debug:
                    template = 'MinimalModbus debug mode. Could not precalculate response size for Modbus {} mode. ' + \
                        'Will read {} bytes. request: {!r}'
                    _print_out(template.format(self.mode, number_of_bytes_to_read, request))


        # Communicate
        response = self._communicate(request, number_of_bytes_to_read)

        # Extract payload
        payloadFromSlave = _extractPayload(response, self.address, self.mode, functioncode)
        return payloadFromSlave


    def _communicate(self, request, number_of_bytes_to_read):
        _checkString(request, minlength=1, description='request')
        _checkInt(number_of_bytes_to_read)

        if self.debug:
            _print_out('\nMinimalModbus debug mode. Writing to instrument (expecting {} bytes back): {!r} ({})'. \
                format(number_of_bytes_to_read, request, _hexlify(request)))


        #self.serial.flushInput() TODO

        # Sleep to make sure 3.5 character times have passed
        minimum_silent_period   = _calculate_minimum_silent_period(self.baudrate)
        time_since_read         = time.ticks_ms() - _LATEST_READ_TIMES.get(self.port, 0)

        if time_since_read < minimum_silent_period:
            sleep_time = minimum_silent_period - time_since_read

            if self.debug:
                template = 'MinimalModbus debug mode. Sleeping for {:.1f} ms. ' + \
                        'Minimum silent period: {:.1f} ms, time since read: {:.1f} ms.'
                text = template.format(
                    sleep_time * _SECONDS_TO_MILLISECONDS,
                    minimum_silent_period * _SECONDS_TO_MILLISECONDS,
                    time_since_read * _SECONDS_TO_MILLISECONDS)
                _print_out(text)

            time.sleep_ms(sleep_time)

        elif self.debug:
            template = 'MinimalModbus debug mode. No sleep required before write. ' + \
                'Time since previous read: {:.1f} ms, minimum silent period: {:.2f} ms.'
            text = template.format(
                time_since_read * _SECONDS_TO_MILLISECONDS,
                minimum_silent_period * _SECONDS_TO_MILLISECONDS)
            _print_out(text)

        # Write request
        latest_write_time = time.ticks_ms()
        
        self.serial.write(request)

        # Read and discard local echo
        if self.handle_local_echo:
            localEchoToDiscard = self.serial.read(len(request))
            if self.debug:
                template = 'MinimalModbus debug mode. Discarding this local echo: {!r} ({} bytes).' 
                text = template.format(localEchoToDiscard, len(localEchoToDiscard))
                _print_out(text)
            if localEchoToDiscard != request:
                template = 'Local echo handling is enabled, but the local echo does not match the sent request. ' + \
                    'Request: {!r} ({} bytes), local echo: {!r} ({} bytes).' 
                text = template.format(request, len(request), localEchoToDiscard, len(localEchoToDiscard))
                raise IOError(text)

        # Read response
        answer = self.serial.read(number_of_bytes_to_read)
        _LATEST_READ_TIMES[self.port] = time.ticks_ms()

#        if sys.version_info[0] > 2:
#            answer = str(answer,'normal')  # Convert types to make it Python3 compatible

        if self.debug:
            template = 'MinimalModbus debug mode. Response from instrument: {!r} ({}) ({} bytes), ' + \
                'roundtrip time: {:.1f} ms. Timeout setting: {:.1f} ms.\n'
            text = template.format(
                answer,
                _hexlify(answer),
                len(answer),
                (_LATEST_READ_TIMES.get(self.port, 0) - latest_write_time) * _SECONDS_TO_MILLISECONDS,
                self.timeout * _SECONDS_TO_MILLISECONDS)
            _print_out(text)

        if len(answer) == 0:
            raise IOError('No communication with the instrument (no answer)')

        return answer

####################
# Payload handling #
####################


def _embedPayload(slaveaddress, mode, functioncode, payloaddata):
    _checkSlaveaddress(slaveaddress)
    _checkMode(mode)
    _checkFunctioncode(functioncode, None)
    _checkString(payloaddata, description='payload')

    firstPart = _numToOneByteArray(slaveaddress) + _numToOneByteArray(functioncode) + payloaddata

    request = firstPart + _calculateCrcString(firstPart)

    return request


def _extractPayload(response, slaveaddress, mode, functioncode):
    BYTEPOSITION_FOR_SLAVEADDRESS          = 0  # Relative to (stripped) response
    BYTEPOSITION_FOR_FUNCTIONCODE          = 1

    NUMBER_OF_RESPONSE_STARTBYTES          = 2  # Number of bytes before the response payload (in stripped response)
    NUMBER_OF_CRC_BYTES                    = 2
    BITNUMBER_FUNCTIONCODE_ERRORINDICATION = 7

    MINIMAL_RESPONSE_LENGTH_RTU            = NUMBER_OF_RESPONSE_STARTBYTES + NUMBER_OF_CRC_BYTES

    # Argument validity testing
#    _checkString(response, description='response')
    _checkSlaveaddress(slaveaddress)
    _checkMode(mode)
    _checkFunctioncode(functioncode, None)

    plainresponse = response

    # Validate response length
    if len(response) < MINIMAL_RESPONSE_LENGTH_RTU:
        raise ValueError('Too short Modbus RTU response (minimum length {} bytes). Response: {!r}'.format( \
            MINIMAL_RESPONSE_LENGTH_RTU,
            response))

    # Validate response checksum
    calculateChecksum = _calculateCrcString
    numberOfChecksumBytes = NUMBER_OF_CRC_BYTES

    receivedChecksum = response[-numberOfChecksumBytes:]
    responseWithoutChecksum = response[0 : len(response) - numberOfChecksumBytes]

    calculatedChecksum = calculateChecksum(responseWithoutChecksum)

    if receivedChecksum != calculatedChecksum:
        template = 'Checksum error in {} mode: {!r} instead of {!r} . The response is: {!r} (plain response: {!r})'
        text = template.format(
                mode,
                receivedChecksum,
                calculatedChecksum,
                response, plainresponse)
        raise ValueError(text)

    # Check slave address
#    responseaddress = ord(response[BYTEPOSITION_FOR_SLAVEADDRESS])

    responseaddress = response[BYTEPOSITION_FOR_SLAVEADDRESS]
    if responseaddress != slaveaddress:
        raise ValueError('Wrong return slave address: {} instead of {}. The response is: {!r}'.format( \
            responseaddress, slaveaddress, response))

    # Check function code
#    receivedFunctioncode = ord(response[BYTEPOSITION_FOR_FUNCTIONCODE])

    receivedFunctioncode = response[BYTEPOSITION_FOR_FUNCTIONCODE]
    if receivedFunctioncode == _setBitOn(functioncode, BITNUMBER_FUNCTIONCODE_ERRORINDICATION):
        raise ValueError('The slave is indicating an error. The response is: {!r}'.format(response))

    elif receivedFunctioncode != functioncode:
        raise ValueError('Wrong functioncode: {} instead of {}. The response is: {!r}'.format( \
            receivedFunctioncode, functioncode, response))

    # Read data payload
    firstDatabyteNumber = NUMBER_OF_RESPONSE_STARTBYTES

    lastDatabyteNumber = len(response) - NUMBER_OF_CRC_BYTES

    payload = response[firstDatabyteNumber:lastDatabyteNumber]
    return bytearray(payload)
############################################
## Serial communication utility functions ##
############################################


def _predictResponseSize(mode, functioncode, payloadToSlave):
    MIN_PAYLOAD_LENGTH = 4  # For implemented functioncodes here

    NUMBER_OF_PAYLOAD_BYTES_IN_WRITE_CONFIRMATION = 4
    NUMBER_OF_PAYLOAD_BYTES_FOR_BYTECOUNTFIELD = 1

    RTU_TO_ASCII_PAYLOAD_FACTOR = 2

    NUMBER_OF_RTU_RESPONSE_STARTBYTES   = 2
    NUMBER_OF_RTU_RESPONSE_ENDBYTES     = 2

    # Argument validity testing
    _checkMode(mode)
    _checkFunctioncode(functioncode, None)
    _checkString(payloadToSlave, description='payload', minlength=MIN_PAYLOAD_LENGTH)

    # Calculate payload size
    if functioncode in [5, 6, 15, 16]:
        response_payload_size = NUMBER_OF_PAYLOAD_BYTES_IN_WRITE_CONFIRMATION

    elif functioncode in [1, 2, 3, 4]:
        given_size = _twoByteStringToNum(payloadToSlave[2:4])
        if functioncode == 1 or functioncode == 2:
            # Algorithm from MODBUS APPLICATION PROTOCOL SPECIFICATION V1.1b
            number_of_inputs = given_size
            response_payload_size = NUMBER_OF_PAYLOAD_BYTES_FOR_BYTECOUNTFIELD + \
                                    number_of_inputs // 8 + (1 if number_of_inputs % 8 else 0)

        elif functioncode == 3 or functioncode == 4:
            number_of_registers = given_size
            response_payload_size = NUMBER_OF_PAYLOAD_BYTES_FOR_BYTECOUNTFIELD + \
                                    number_of_registers * _NUMBER_OF_BYTES_PER_REGISTER

    else:
        raise ValueError('Wrong functioncode: {}. The payload is: {!r}'.format( \
            functioncode, payloadToSlave))

    # Calculate number of bytes to read
    return NUMBER_OF_RTU_RESPONSE_STARTBYTES + \
        response_payload_size + \
        NUMBER_OF_RTU_RESPONSE_ENDBYTES


def _calculate_minimum_silent_period(baudrate):
    _checkNumerical(baudrate, minvalue=1, description='baudrate')  # Avoid division by zero

    BITTIMES_PER_CHARACTERTIME = 11
    MINIMUM_SILENT_CHARACTERTIMES = 3.5

    bittime = 1000 / float(baudrate)
    return bittime * BITTIMES_PER_CHARACTERTIME * MINIMUM_SILENT_CHARACTERTIMES

##############################
# String and num conversions #
##############################

def _numToOneByteArray(inputvalue):
    _checkInt(inputvalue, minvalue=0, maxvalue=0xFF)
    outstring = bytearray(1)
    outstring[0] = inputvalue
    return outstring


def _numToTwoByteArray(value, numberOfDecimals=0, LsbFirst=False, signed=False):
    _checkNumerical(value, description='inputvalue')
    _checkInt(numberOfDecimals, minvalue=0, description='number of decimals')
    _checkBool(LsbFirst, description='LsbFirst')
    _checkBool(signed, description='signed parameter')

    multiplier = 10 ** numberOfDecimals
    integer = int(float(value) * multiplier)

    if LsbFirst:
        formatcode = '<'  # Little-endian
    else:
        formatcode = '>'  # Big-endian
    if signed:
        formatcode += 'h'  # (Signed) short (2 bytes)
    else:
        formatcode += 'H'  # Unsigned short (2 bytes)

    outstring = _pack(formatcode, integer)
    assert len(outstring) == 2
    return outstring


def _twoByteStringToNum(bytearray, numberOfDecimals=0, signed=False):
    _checkString(bytearray, minlength=2, maxlength=2, description='bytearray')
    _checkInt(numberOfDecimals, minvalue=0, description='number of decimals')
    _checkBool(signed, description='signed parameter')

    formatcode = '>'  # Big-endian
    if signed:
        formatcode += 'h'  # (Signed) short (2 bytes)
    else:
        formatcode += 'H'  # Unsigned short (2 bytes)

    fullregister = _unpack(formatcode, bytearray)

    if numberOfDecimals == 0:
        return fullregister
    divisor = 10 ** numberOfDecimals
    return fullregister / float(divisor)


def _valuelistToBytestring(valuelist, numberOfRegisters):
    MINVALUE = 0
    MAXVALUE = 65535

    _checkInt(numberOfRegisters, minvalue=1, description='number of registers')

    if not isinstance(valuelist, list):
        raise TypeError('The valuelist parameter must be a list. Given {0!r}.'.format(valuelist))

    for value in valuelist:
        _checkInt(value, minvalue=MINVALUE, maxvalue=MAXVALUE, description='elements in the input value list')

    _checkInt(len(valuelist), minvalue=numberOfRegisters, maxvalue=numberOfRegisters, \
        description='length of the list')

    numberOfBytes = _NUMBER_OF_BYTES_PER_REGISTER * numberOfRegisters

    bytearray = ''
    for value in valuelist:
        bytearray += _numToTwoByteArray(value, signed=False)

    assert len(bytearray) == numberOfBytes
    return bytearray


def _bytearrayToValuelist(bytearray, numberOfRegisters):
    _checkInt(numberOfRegisters, minvalue=1, description='number of registers')
    numberOfBytes = _NUMBER_OF_BYTES_PER_REGISTER * numberOfRegisters
    _checkString(bytearray, 'byte string', minlength=numberOfBytes, maxlength=numberOfBytes)

    values = []
    for i in range(numberOfRegisters):
        offset = _NUMBER_OF_BYTES_PER_REGISTER * i
        substring = bytearray[offset : offset + _NUMBER_OF_BYTES_PER_REGISTER]
        values.append(_twoByteStringToNum(substring))

    return values


def _pack(formatstring, value):
    try:
        result = struct.pack(formatstring, value)
    except:
        errortext = 'The value to send is probably out of range, as the num-to-bytearray conversion failed.'
        errortext += ' Value: {0!r} Struct format code is: {1}'
        raise ValueError(errortext.format(value, formatstring))

    return bytearray(result)

def _unpack(formatstring, packed):
    _checkString(packed, description='packed string', minlength=1)

    try:
        value = struct.unpack(formatstring, packed)[0]
    except:
        errortext = 'The received bytearray is probably wrong, as the bytearray-to-num conversion failed.'
        errortext += ' Bytestring: {0!r} Struct format code is: {1}'
        raise ValueError(errortext.format(packed, formatstring))

    return value


def _hexencode(bytearray, insert_spaces = False):
    separator = '' if not insert_spaces else ' '
    
    # Use plain string formatting instead of binhex.hexlify,
    # in order to have it Python 2.x and 3.x compatible

    byte_representions = []
    for c in bytearray:
        byte_representions.append( '{0:02X}'.format(c) )
    return separator.join(byte_representions).strip()


def _hexlify(bytearray):
    return _hexencode(bytearray, insert_spaces = True)


def _bitResponseToValue(bytearray):
    _checkString(bytearray, description='bytearray', minlength=1, maxlength=1)

    RESPONSE_ON  = '\x01'
    RESPONSE_OFF = '\x00'

    if bytearray == RESPONSE_ON:
        return 1
    elif bytearray == RESPONSE_OFF:
        return 0
    else:
        raise ValueError('Could not convert bit response to a value. Input: {0!r}'.format(bytearray))


def _createBitpattern(functioncode, value):
    _checkFunctioncode(functioncode, [5, 15])
    _checkInt(value, minvalue=0, maxvalue=1, description='inputvalue')

    if functioncode == 5:
        if value == 0:
            return '\x00\x00'
        else:
            return '\xff\x00'

    elif functioncode == 15:
        if value == 0:
            return '\x00'
        else:
            return '\x01'  # Is this correct??


####################
# Bit manipulation #
####################

def _setBitOn(x, bitNum):
    _checkInt(x, minvalue=0, description='input value')
    _checkInt(bitNum, minvalue=0, description='bitnumber')

    return x | (1 << bitNum)

############################
# Error checking functions #
############################

_CRC16TABLE = (
        0, 49345, 49537,   320, 49921,   960,   640, 49729, 50689,  1728,  1920, 
    51009,  1280, 50625, 50305,  1088, 52225,  3264,  3456, 52545,  3840, 53185, 
    52865,  3648,  2560, 51905, 52097,  2880, 51457,  2496,  2176, 51265, 55297, 
     6336,  6528, 55617,  6912, 56257, 55937,  6720,  7680, 57025, 57217,  8000, 
    56577,  7616,  7296, 56385,  5120, 54465, 54657,  5440, 55041,  6080,  5760, 
    54849, 53761,  4800,  4992, 54081,  4352, 53697, 53377,  4160, 61441, 12480, 
    12672, 61761, 13056, 62401, 62081, 12864, 13824, 63169, 63361, 14144, 62721, 
    13760, 13440, 62529, 15360, 64705, 64897, 15680, 65281, 16320, 16000, 65089, 
    64001, 15040, 15232, 64321, 14592, 63937, 63617, 14400, 10240, 59585, 59777, 
    10560, 60161, 11200, 10880, 59969, 60929, 11968, 12160, 61249, 11520, 60865, 
    60545, 11328, 58369,  9408,  9600, 58689,  9984, 59329, 59009,  9792,  8704, 
    58049, 58241,  9024, 57601,  8640,  8320, 57409, 40961, 24768, 24960, 41281, 
    25344, 41921, 41601, 25152, 26112, 42689, 42881, 26432, 42241, 26048, 25728, 
    42049, 27648, 44225, 44417, 27968, 44801, 28608, 28288, 44609, 43521, 27328, 
    27520, 43841, 26880, 43457, 43137, 26688, 30720, 47297, 47489, 31040, 47873, 
    31680, 31360, 47681, 48641, 32448, 32640, 48961, 32000, 48577, 48257, 31808, 
    46081, 29888, 30080, 46401, 30464, 47041, 46721, 30272, 29184, 45761, 45953, 
    29504, 45313, 29120, 28800, 45121, 20480, 37057, 37249, 20800, 37633, 21440, 
    21120, 37441, 38401, 22208, 22400, 38721, 21760, 38337, 38017, 21568, 39937, 
    23744, 23936, 40257, 24320, 40897, 40577, 24128, 23040, 39617, 39809, 23360, 
    39169, 22976, 22656, 38977, 34817, 18624, 18816, 35137, 19200, 35777, 35457, 
    19008, 19968, 36545, 36737, 20288, 36097, 19904, 19584, 35905, 17408, 33985, 
    34177, 17728, 34561, 18368, 18048, 34369, 33281, 17088, 17280, 33601, 16640, 
    33217, 32897, 16448)


def _calculateCrcString(inputstring):
    # Preload a 16-bit register with ones
    register = 0xFFFF

    try:
        _checkString(inputstring, description='input CRC string')
        for char in inputstring:
            register = (register >> 8) ^ _CRC16TABLE[(register ^ char) & 0xFF]
    except:
        for char in inputstring:
            register = (register >> 8) ^ _CRC16TABLE[(register ^ char) & 0xFF]

    return _numToTwoByteArray(register, LsbFirst=True)


def _checkMode(mode):
    if not isinstance(mode, str):
        raise TypeError('The {0} should be a string. Given: {1!r}'.format("mode", mode))

    if mode not in [MODE_RTU]:
        raise ValueError("Unreconized Modbus mode given. Must be 'rtu' or 'ascii' but {0!r} was given.".format(mode))


def _checkFunctioncode(functioncode, listOfAllowedValues=[]):
    FUNCTIONCODE_MIN = 1
    FUNCTIONCODE_MAX = 127

    _checkInt(functioncode, FUNCTIONCODE_MIN, FUNCTIONCODE_MAX, description='functioncode')

    if listOfAllowedValues is None:
        return

    if not isinstance(listOfAllowedValues, list):
        raise TypeError('The listOfAllowedValues should be a list. Given: {0!r}'.format(listOfAllowedValues))

    for value in listOfAllowedValues:
        _checkInt(value, FUNCTIONCODE_MIN, FUNCTIONCODE_MAX, description='functioncode inside listOfAllowedValues')

    if functioncode not in listOfAllowedValues:
        raise ValueError('Wrong function code: {0}, allowed values are {1!r}'.format(functioncode, listOfAllowedValues))


def _checkSlaveaddress(slaveaddress):
    SLAVEADDRESS_MAX = 247
    SLAVEADDRESS_MIN = 0

    _checkInt(slaveaddress, SLAVEADDRESS_MIN, SLAVEADDRESS_MAX, description='slaveaddress')


def _checkRegisteraddress(registeraddress):
    REGISTERADDRESS_MAX = 0xFFFF
    REGISTERADDRESS_MIN = 0

    _checkInt(registeraddress, REGISTERADDRESS_MIN, REGISTERADDRESS_MAX, description='registeraddress')


def _checkResponseByteCount(payload):
    POSITION_FOR_GIVEN_NUMBER = 0
    NUMBER_OF_BYTES_TO_SKIP = 1

    _checkString(payload, minlength=1, description='payload')

    givenNumberOfDatabytes = payload[POSITION_FOR_GIVEN_NUMBER]
    countedNumberOfDatabytes = len(payload) - NUMBER_OF_BYTES_TO_SKIP

    if givenNumberOfDatabytes != countedNumberOfDatabytes:
        errortemplate = 'Wrong given number of bytes in the response: {0}, but counted is {1} as data payload length is {2}.' + \
            ' The data payload is: {3!r}'
        errortext = errortemplate.format(givenNumberOfDatabytes, countedNumberOfDatabytes, len(payload), payload)
        raise ValueError(errortext)


def _checkResponseRegisterAddress(payload, registeraddress):
    _checkString(payload, minlength=2, description='payload')
    _checkRegisteraddress(registeraddress)

    bytesForStartAddress = payload[0:2]
    receivedStartAddress = _twoByteStringToNum(bytesForStartAddress)

    if receivedStartAddress != registeraddress:
        raise ValueError('Wrong given write start adress: {0}, but commanded is {1}. The data payload is: {2!r}'.format( \
            receivedStartAddress, registeraddress, payload))


def _checkResponseNumberOfRegisters(payload, numberOfRegisters):
    _checkString(payload, minlength=4, description='payload')
    _checkInt(numberOfRegisters, minvalue=1, maxvalue=0xFFFF, description='numberOfRegisters')

    bytesForNumberOfRegisters = payload[2:4]
    receivedNumberOfWrittenReisters = _twoByteStringToNum(bytesForNumberOfRegisters)

    if receivedNumberOfWrittenReisters != numberOfRegisters:
        raise ValueError('Wrong number of registers to write in the response: {0}, but commanded is {1}. The data payload is: {2!r}'.format( \
            receivedNumberOfWrittenReisters, numberOfRegisters, payload))


def _checkResponseWriteData(payload, writedata):
    _checkString(payload, minlength=4, description='payload')
    _checkString(writedata, minlength=2, maxlength=2, description='writedata')

    receivedWritedata = payload[2:4]

    if receivedWritedata != writedata:
        raise ValueError('Wrong write data in the response: {0!r}, but commanded is {1!r}. The data payload is: {2!r}'.format( \
            receivedWritedata, writedata, payload))


def _checkString(inputstring, description, minlength=0, maxlength=None):
    # Type checking
    if not isinstance(description, str):
        raise TypeError('The description should be a string. Given: {0!r}'.format(description))

    if not isinstance(inputstring, bytearray):
        raise TypeError('The {0} should be a string. Given: {1!r}'.format(description, inputstring))

    if not isinstance(maxlength, (int, type(None))):
        raise TypeError('The maxlength must be an integer or None. Given: {0!r}'.format(maxlength))

    # Check values
    _checkInt(minlength, minvalue=0, maxvalue=None, description='minlength')

    if len(inputstring) < minlength:
        raise ValueError('The {0} is too short: {1}, but minimum value is {2}. Given: {3!r}'.format( \
            description, len(inputstring), minlength, inputstring))

    if not maxlength is None:
        if maxlength < 0:
            raise ValueError('The maxlength must be positive. Given: {0}'.format(maxlength))

        if maxlength < minlength:
            raise ValueError('The maxlength must not be smaller than minlength. Given: {0} and {1}'.format( \
                maxlength, minlength))

        if len(inputstring) > maxlength:
            raise ValueError('The {0} is too long: {1}, but maximum value is {2}. Given: {3!r}'.format( \
                description, len(inputstring), maxlength, inputstring))


def _checkInt(inputvalue, minvalue=None, maxvalue=None, description='inputvalue'):
    if not isinstance(description, str):
        raise TypeError('The description should be a string. Given: {0!r}'.format(description))

    if not isinstance(inputvalue, (int, long)):
        raise TypeError('The {0} must be an integer. Given: {1!r}'.format(description, inputvalue))

    if not isinstance(minvalue, (int, long, type(None))):
        raise TypeError('The minvalue must be an integer or None. Given: {0!r}'.format(minvalue))

    if not isinstance(maxvalue, (int, long, type(None))):
        raise TypeError('The maxvalue must be an integer or None. Given: {0!r}'.format(maxvalue))

    _checkNumerical(inputvalue, minvalue, maxvalue, description)


def _checkNumerical(inputvalue, minvalue=None, maxvalue=None, description='inputvalue'):
    # Type checking
    if not isinstance(description, str):
        raise TypeError('The description should be a string. Given: {0!r}'.format(description))

    if not isinstance(inputvalue, (int, long, float)):
        raise TypeError('The {0} must be numerical. Given: {1!r}'.format(description, inputvalue))

    if not isinstance(minvalue, (int, float, long, type(None))):
        raise TypeError('The minvalue must be numeric or None. Given: {0!r}'.format(minvalue))

    if not isinstance(maxvalue, (int, float, long, type(None))):
        raise TypeError('The maxvalue must be numeric or None. Given: {0!r}'.format(maxvalue))

    # Consistency checking
    if (not minvalue is None) and (not maxvalue is None):
        if maxvalue < minvalue:
            raise ValueError('The maxvalue must not be smaller than minvalue. Given: {0} and {1}, respectively.'.format( \
                maxvalue, minvalue))

    # Value checking
    if not minvalue is None:
        if inputvalue < minvalue:
            raise ValueError('The {0} is too small: {1}, but minimum value is {2}.'.format( \
                description, inputvalue, minvalue))

    if not maxvalue is None:
        if inputvalue > maxvalue:
            raise ValueError('The {0} is too large: {1}, but maximum value is {2}.'.format( \
                description, inputvalue, maxvalue))


def _checkBool(inputvalue, description='inputvalue'):
    if not isinstance(inputvalue, bool):
        raise TypeError('The {0} must be boolean. Given: {1!r}'.format(description, inputvalue))

#####################
# Development tools #
#####################


def _print_out(inputstring):
    sys.stdout.write(inputstring + '\n')


