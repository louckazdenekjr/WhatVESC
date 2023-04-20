from datetime import datetime, time
import asyncio
import bleak
import struct

from PyCRC.CRCCCITT import CRCCCITT

from os import system


UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

config = {
	"address": "None",
	"cell_series": "15",
	"cell_min": "2.7",
	"cell_max": "4.2",
	"unit": "kmh",
}

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
				mostemp : float = struct.unpack(">H", packet.payload[5:7])[0] / 10
				mottemp : float = struct.unpack(">H", packet.payload[7:9])[0] / 10
				current : float = struct.unpack(">i", packet.payload[9:13])[0] / 100
				dutycycle : float = struct.unpack(">h", packet.payload[13:15])[0] / 10
				speed : float = (struct.unpack(">i", packet.payload[15:19])[0] / 1000) * conversion_factor
				voltage : float = struct.unpack(">H", packet.payload[19:21])[0] / 10

				cell_series : int = int(config["cell_series"])
				cellv : float = voltage / cell_series
				cell_min : float = float(config["cell_min"])
				cell_max : float = float(config["cell_max"])
				batp : float = (cellv-cell_min)/(cell_max-cell_min)*100
				duty = int(round(abs(dutycycle), 0))
				percent_bat = int(round(min(batp, 100), 0))

				print(f"Speed: {abs(speed):.1f} {unit_s}")
				print(f"Duty cycle: {duty}%")
				
				print(f"Pack Voltage: {voltage:.2f} V")
				print(f"Average cell voltage: {cellv:.2f} V")
				print(f"Battery current: {current:.2f} A")
				print(f"Battery percentage: {percent_bat}%")
				print(f"Temp FET: {mostemp:.0f}°C")
				print(f"Temp MOT: {mottemp:.0f}°C")

				for x in range(10):
					print()
						
	# TODO: we can connect directly, but for now we scan first
	while True:
		try:
			# scan for devices
			if True:
				scanned_uarts = []
				def find_uart_device(device, adv):
					if UART_SERVICE_UUID.lower() in adv.service_uuids and device.address not in scanned_uarts:
						scanned_uarts.append(device.address)
				
				# configure scanner
				scanner = bleak.BleakScanner(find_uart_device)
				scanned_uarts.clear()
				
				# scan for uart
				print("Scanning for BLE UART interfaces:")
				await scanner.start()
				await asyncio.sleep(5.0)
				await scanner.stop()
				for uart in scanned_uarts:
					print(f"\t{uart}")

				await asyncio.sleep(5)

			if len(scanned_uarts) > 0:
				print("Connecting to first BLE UART.")
			else:
				print("No BLE UART found.")
				exit()	

			async with bleak.BleakClient(scanned_uarts[0]) as client:
				await client.start_notify(UART_TX_CHAR_UUID, handle_rx)
				if client.is_connected:
					print("Connected to VESC.") # TODO: implement check

				while True:
					await asyncio.sleep(1/5)
					if client.is_connected:
						await client.write_gatt_char(UART_RX_CHAR_UUID, bytearray(packet_get_values.packet))
		except bleak.exc.BleakError as e:
			print(f"error: {e}")
			await asyncio.sleep(1)
		except asyncio.exceptions.TimeoutError as e:
			print(f"error: async Timeout Error")
			await asyncio.sleep(1)



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


if __name__ == "__main__":
	asyncio.run(bluetooth())
