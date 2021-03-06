import time
import pytest
import logging
import nvme as d

def test_hello_world(nvme0, nvme0n1: d.Namespace):
    read_buf = d.Buffer(512)
    data_buf = d.Buffer(512)
    data_buf[10:21] = b'hello world'
    qpair = d.Qpair(nvme0, 16)  # create IO SQ/CQ pair, with 16 queue-depth
    assert read_buf[10:21] != b'hello world'

    def write_cb(cdw0, status1):  # command callback function
        nvme0n1.read(qpair, read_buf, 0, 1)
    nvme0n1.write(qpair, data_buf, 0, 1, cb=write_cb)
    qpair.waitdone(2)
    assert read_buf[10:21] == b'hello world'

    
from pytemperature import k2c

def test_ioworker_with_temperature(nvme0, nvme0n1):
    smart_log = d.Buffer(512, "smart log")
    with nvme0n1.ioworker(io_size=8, lba_align=16,
                          lba_random=True, qdepth=16,
                          read_percentage=0, time=30):
        for i in range(40):
            nvme0.getlogpage(0x02, smart_log, 512).waitdone()
            ktemp = smart_log.data(2, 1)
            logging.info("temperature: %0.2f degreeC" % k2c(ktemp))
            time.sleep(1)

            
def test_trim_basic(nvme0: d.Controller, nvme0n1: d.Namespace, verify):
    GB = 1024*1024*1024
    all_zeor_databuf = d.Buffer(512)
    trimbuf = d.Buffer(4096)
    q = d.Qpair(nvme0, 32)

    # DUT info
    logging.info("model number: %s" % nvme0.id_data(63, 24, str))
    logging.info("firmware revision: %s" % nvme0.id_data(71, 64, str))

    # write
    logging.info("write data in 10G ~ 20G")
    io_size = 128*1024//512
    start_lba = 10*GB//512
    lba_count = 10*GB//512
    nvme0n1.ioworker(io_size = io_size,
                     lba_align = io_size,
                     lba_random = False, 
                     read_percentage = 0, 
                     lba_start = start_lba,
                     io_count = lba_count//io_size,
                     qdepth = 128).start().close()

    # verify data after write, data should be modified
    with pytest.warns(UserWarning, match="ERROR status: 02/85"):
        nvme0n1.compare(q, all_zeor_databuf, start_lba).waitdone()

    # get the empty trim time
    trimbuf.set_dsm_range(0, 0, 0)
    trim_cmd = nvme0n1.dsm(q, trimbuf, 1).waitdone() # first call is longer, due to cache?
    start_time = time.time()
    trim_cmd = nvme0n1.dsm(q, trimbuf, 1).waitdone()
    empty_trim_time = time.time()-start_time

    # the trim time on device-side only
    logging.info("trim the 10G data from LBA 0x%lx" % start_lba)
    trimbuf.set_dsm_range(0, start_lba, lba_count)
    start_time = time.time()
    trim_cmd = nvme0n1.dsm(q, trimbuf, 1).waitdone()
    trim_time = time.time()-start_time-empty_trim_time
    logging.info("trim bandwidth: %0.2fGB/s" % (10/trim_time))

    # verify after trim
    nvme0n1.compare(q, all_zeor_databuf, start_lba).waitdone()
