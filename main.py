from datetime import datetime, time
import asyncio
import bleak
import struct
from PyCRC.CRCCCITT import CRCCCITT
import curses


UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"


#----------------------------------------
# CONFIG VALUES
config = {
	"address": "none", # -> "D6:27:82:98:07:D1"
	"cell_series": "15",
	"cell_min": "2.7",
	"cell_max": "4.2",
	"unit": "kmh",
}
#----------------------------------------


class Terminal:
	
	def __init__(self):
		self.screen = curses.initscr()
		curses.noecho()
		curses.cbreak()
		curses.curs_set(0)
		self.current_line = 0
		
		
	def unload(self):
		curses.echo()
		curses.nocbreak()
		curses.curs_set(1)
		curses.endwin()
		
		
	def reportLine(self, text):
		self.screen.addstr(self.current_line, 0, text)
		self.current_line += 1
		self.screen.refresh()
		
	
	def erase(self):
		self.screen.erase()


	def reportMetrics(self, data):
		for x in range(len(data)):
			self.screen.addstr(x, 0, data[x].ljust(40))
			
		self.screen.refresh()


async def bluetooth():
	# create packet that requests current_in(), speed, voltage_in, battery_level from COMM_GET_VALUES_SETUP_SELECTIVE
	packet_get_values = Packet()
	packet_get_values.size = 2
	packet_get_values.payload = struct.pack(">BI", 51, (1 << 0) | (1 << 1) | (1 << 3) | (1 << 4) | (1 << 6) | (1 << 7))
	packet_get_values.encode()

	def handle_rx(_: int, data: bytearray):
		buffer = Buffer()
		buffer.extend(data)
		found, packet = buffer.next_packet()

		if found:
			if packet.payload[0:len(packet_get_values.payload)] == packet_get_values.payload:
				if config["unit"] == "kmh":
					conversion_factor = 3.6
					unit_s = "km/h"
				else:
					conversion_factor = 2.237
					unit_s = "mph"
				mostemp = struct.unpack(">H", packet.payload[5:7])[0] / 10
				mottemp = struct.unpack(">H", packet.payload[7:9])[0] / 10
				current = struct.unpack(">i", packet.payload[9:13])[0] / 100
				dutycycle = struct.unpack(">h", packet.payload[13:15])[0] / 10
				speed = (struct.unpack(">i", packet.payload[15:19])[0] / 1000) * conversion_factor
				voltage = struct.unpack(">H", packet.payload[19:21])[0] / 10

				cell_series = int(config["cell_series"])
				cellv = voltage / cell_series
				cell_min = float(config["cell_min"])
				cell_max = float(config["cell_max"])
				batp = (cellv-cell_min)/(cell_max-cell_min)*100
				duty = int(round(abs(dutycycle), 0))
				percent_bat = int(round(min(batp, 100), 0))

				data = []
				data.append(f"Speed: {abs(speed):.1f} {unit_s}")
				data.append(f"Duty cycle: {duty}%")
				data.append(f"Pack Voltage: {voltage:.2f} V")
				data.append(f"Average cell voltage: {cellv:.2f} V")
				data.append(f"Battery current: {current:.2f} A")
				data.append(f"Battery percentage: {percent_bat}%")
				data.append(f"Temp FET: {mostemp:.0f} °C")
				data.append(f"Temp MOT: {mottemp:.0f} °C")
				terminal.reportMetrics(data)
				
	if config["address"] != "none":
		# use configured address for connection
		target_addr = config["address"]
		
		# show user message
		terminal.reportLine(f"Connecting to: {target_addr}")
	else:
		terminal.reportLine("No address provided.")
	
		# scan for devices
		scanned_uarts = []
		def find_uart_device(device, adv):
			#screen.reportLine("Scanning.")
			if UART_SERVICE_UUID.lower() in adv.service_uuids and device not in scanned_uarts:
				scanned_uarts.append(device)
		
		# configure scanner
		scanner = bleak.BleakScanner(find_uart_device)
		scanned_uarts.clear()
		
		# scan for uart
		terminal.reportLine("Scanning for BLE UART interfaces:")
		await scanner.start()
		await asyncio.sleep(5.0)
		await scanner.stop()
		
		# display devices
		for uart in scanned_uarts:
			terminal.reportLine(f"\t{uart}")
		await asyncio.sleep(5)

		if len(scanned_uarts) > 0:
			terminal.reportLine("Connecting to first BLE UART.")
		else:
			terminal.reportLine("No BLE UART found. Exiting now.")
			await asyncio.sleep(5)
			exit()	
			
		target_addr = scanned_uarts[0].address

	try:
		# proceed with connection
		async with bleak.BleakClient(target_addr) as client:
			await client.start_notify(UART_TX_CHAR_UUID, handle_rx)
			if client.is_connected:
				terminal.reportLine("Connected to VESC.") # TODO: implement check
				await asyncio.sleep(1)

			terminal.erase()
			while True:
				if client.is_connected:
					await client.write_gatt_char(UART_RX_CHAR_UUID, bytearray(packet_get_values.packet))
					
				# poll rate 200 msf
				await asyncio.sleep(0.2) 
	except bleak.exc.BleakError as e:
		screen.reportLine(f"error: {e}")
		await asyncio.sleep(5)
	except asyncio.exceptions.TimeoutError as e:
		screen.reportLine(f"error: async Timeout Error")
		await asyncio.sleep(5)


class Buffer:

	"""Vesc Buffer loads and finds packets"""
	def __init__(self):
		self.__buffer : bytearray = bytearray()


	def extend(self, data: bytearray):
		self.__buffer.extend(data)


	def clear(self, data: bytearray):
		self.__buffer.clear()


	@property
	def buffer(self):
		return bytearray(self.__buffer)


	def next_packet(self):
		packet_exists = False
		packet = Packet()

		for v_index, v_byte in enumerate(self.__buffer):
			#check if valid packet
			#fist byte in packet for size mode 2
			if v_byte == 2:
				v_length = self.__buffer[v_index+1]
				v_end = v_index + v_length + 4
				#check for end byte in packet
				if v_end + 1 > len(self.__buffer):
					break

				if self.__buffer[v_end] == 3:
					packet.packet = bytes(self.__buffer[v_index : v_end])
					packet.decode()
					#check crc of packet
					if packet.validate():
						#clear out bad data and proccesed packet from buffer
						packet_exists = True
						del self.__buffer[0:v_end+1]
		
		return packet_exists, packet
		
		
	def __str__(self):
		"""Buffer as string"""
		return " ".join([hex(x) for x in self.__buffer])


#----------------------------------------
class Packet:

	"""Vesc Packet load data then encode or decode"""
	def __init__(self):
		"""Vesc Packet load data then encode or decode. size is 2 for small packets and 3 for large packets. size 2 is the only implemented size so far"""
		self.__size : int = None
		self.__payload : bytes = bytes()
		self.__packet : bytes = bytes()
		self.__crc : bytes = bytes()


	@property
	def size(self):
		return self.__size


	@size.setter
	def size(self, size):
		if (size == 2 or size == 3):
			self.__size = size
		else:
			raise ValueError("Size must be 2 or 3")


	@property
	def payload(self):
		return self.__payload


	@payload.setter
	def payload(self, payload : bytes):
		self.__payload = bytes(payload)


	@property
	def packet(self):
		return self.__packet


	@packet.setter
	def packet(self, packet : bytes):
		self.__packet = bytes(packet)


	@property
	def crc(self):
		return self.__crc


	@crc.setter
	def crc(self, crc : bytes):
		self.__crc = bytes(crc)


	def encode(self):
		self.__crc = struct.pack(">H", CRCCCITT().calculate(self.__payload))
		if self.__size == 2:
			self.__packet = struct.pack(">BB" + str(len(self.__payload)) + "s2sB", self.__size, len(self.__payload), self.__payload, self.__crc, 3)
		elif self.__size == 3:
			# TODO: implement size 3 packets
			pass


	def decode(self):
		self.__size = ord(self.__packet[:1])
		if self.__size == 2:
			length = ord(self.__packet[1:2])
			self.__payload = self.__packet[2:2+length]
			self.__crc = self.__packet[2+length:4+length]
			self.__packet = struct.pack(">BB" + str(len(self.__payload)) + "s2sB", self.__size, len(self.__payload), self.__payload, self.__crc, 3)
		elif self.__size == 3:
			# TODO: implement size 3 packets
			pass


	def validate(self):
		return struct.pack(">H", CRCCCITT().calculate(self.__payload)) == self.__crc


	def __str__(self):
		"""Packet as string"""
		return " ".join([hex(x) for x in self.__packet])
#----------------------------------------


if __name__ == "__main__":
	try:
		# init curses
		terminal = Terminal()
		
		# run main
		asyncio.run(bluetooth())
	except KeyboardInterrupt:
		# handle interrupt gracefully
		exit()
	finally:
		# end curses
		terminal.unload()