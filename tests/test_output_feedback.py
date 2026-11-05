"""Speaker output feedback must wait for actual HA results, keep errors and reject stale responses."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class OutputFeedbackTest(unittest.TestCase):
    def test_choice_confirmation_reconnect_timeout_and_stale_tickets(self):
        source=r'''
#include "styles/glass-v2/output_feedback.h"
#include <cassert>
using namespace output_feedback;
int main(){
 Operation op;
 assert(op.begin(Kind::CHOICE,"JBL","Panelen",100));auto first=op.ticket;
 assert(!op.begin(Kind::RECONNECT,"","Panelen",101)&&op.ticket==first);
 assert(!op.observe("Panelen",200));assert(!op.acknowledge(first,300)&&op.busy());
 assert(op.observe("JBL",400)&&!op.busy()&&op.message()=="Vald: JBL");
 assert(!op.tick(4399)&&op.tick(4400)&&op.message().empty());
 assert(op.begin(Kind::CHOICE,"Panelen","JBL",5000));first=op.ticket;
 assert(!op.observe("Panelen",5100)&&op.busy());assert(op.acknowledge(first,5200));
 // Same choice has no changed-state packet, but its service response still confirms it.
 assert(op.begin(Kind::CHOICE,"Panelen","Panelen",6000));assert(op.acknowledge(op.ticket,6100));
 assert(op.begin(Kind::RECONNECT,"","JBL",7000));first=op.ticket;
 assert(!op.tick(42000)&&op.busy()); // the old fixed 35 s timer cannot establish success
 assert(!op.tick(86999)&&op.tick(87000)&&op.phase==Phase::TIMEOUT);
 assert(op.tick(100000)==false&&!op.message().empty()); // keep a timeout visible
 assert(op.begin(Kind::RECONNECT,"","JBL",100001));auto second=op.ticket;
 assert(first!=second&&!op.fail(first,100002)&&!op.acknowledge(first,100003)&&op.busy());
 assert(op.acknowledge(second,100004)&&op.phase==Phase::DONE);
 assert(op.message().find("ljudet återställs")!=std::string::npos); // no acoustic-recovery claim
 assert(op.begin(Kind::CHOICE,"JBL","Panelen",200000));assert(op.fail(op.ticket,200001));
 assert(!op.tick(999999)&&op.phase==Phase::ERROR);
 assert(op.begin(Kind::CHOICE,"JBL","Panelen",0xfffffff0u));
 assert(!op.tick(100)&&op.tick(uint32_t(0xfffffff0u+45000u)));
}
'''
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'test.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            '-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
