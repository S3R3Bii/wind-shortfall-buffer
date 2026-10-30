"""Invented source-boundary fixtures, excluded from study data and outcomes."""
from datetime import datetime, timedelta, timezone
from fractions import Fraction as Q
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import prepare_inputs as p

class SourceBoundaries(unittest.TestCase):
 def rows(self):
  return [dict(source_file='synthetic.csv',source_row=str(i+1),datetime=(p.START+timedelta(minutes=15*i)).isoformat(),resolutioncode='PT15M',offshoreonshore='Onshore',region=region,gridconnectiontype='Dso',measured=str(10*(i+1)),dayaheadforecast=str(10*(i+2)),monitoredcapacity='1000',decrementalbidid='') for region in ['Flanders','Wallonia'] for i in range(4)]
 def build(self,rows):
  with patch.object(p,'END',p.START+timedelta(hours=1)):return p.build(rows)
 def test_hourly_power_is_average(self):
  _,_,hours,gate=self.build(self.rows());self.assertEqual(hours[0]['measured_exact'],'25/1');self.assertEqual(hours[0]['forecast_exact'],'35/1');self.assertEqual(gate['eligible_hours'],2);self.assertEqual(gate['status'],'PASS')
 def test_missing_quarter_hour_abstains(self):
  raw,slots,hours,gate=self.build(self.rows()[1:]);self.assertFalse(hours[0]['eligible']);self.assertEqual(gate['planned_slots'],8);self.assertEqual(gate['eligible_slots'],7)
 def test_identical_duplicate_not_selected(self):
  rows=self.rows();raw,_,hours,_=self.build(rows+[dict(rows[0])]);self.assertFalse(hours[0]['eligible']);self.assertFalse(raw[0]['eligible']);self.assertFalse(raw[-1]['eligible']);self.assertIn('duplicate_source_key',raw[-1]['reasons'])
 def test_bad_interval_length(self):
  row=self.rows()[0];row['resolutioncode']='PT60M';self.assertIn('wrong_interval_length',p.interval(row)[2])
 def test_nonfinite_and_missing_stay_unavailable(self):
  for token in ['', 'NaN','Infinity','not reported']:
   row=self.rows()[0];row['measured']=token;self.assertIn('measured_unavailable_or_nonfinite',p.interval(row)[2])
 def test_zero_generation_is_valid(self):
  row=self.rows()[0];row['measured']='0';self.assertEqual(p.interval(row)[2],[])
 def test_zero_capacity_invalid(self):
  row=self.rows()[0];row['monitoredcapacity']='0';self.assertIn('monitoredcapacity_outside_sign_rule',p.interval(row)[2])
 def test_forecast_error_does_not_screen_row(self):
  row=self.rows()[0];row['measured']='1000000';row['dayaheadforecast']='0';self.assertEqual(p.interval(row)[2],[])
 def test_naive_or_unaligned_timestamp_rejected(self):
  for text in ['2018-01-01T00:00:00','2018-01-01T00:01:00Z']:
   row=self.rows()[0];row['datetime']=text;self.assertTrue(p.interval(row)[2])
 def test_timezone_rollover(self):
  row=self.rows()[0];row['datetime']='2018-01-01T01:00:00+01:00';self.assertEqual(p.interval(row)[0],p.START)
 def test_hour_cannot_use_duplicate_times(self):
  v=dict(measured=Q(1),dayaheadforecast=Q(2),monitoredcapacity=Q(10))
  with self.assertRaises(ValueError):p.hourly_mean([(p.START,v)]*4)
 def test_hour_cannot_cross_an_hour_boundary(self):
  v=dict(measured=Q(1),dayaheadforecast=Q(2),monitoredcapacity=Q(10))
  with self.assertRaises(ValueError):p.hourly_mean([(p.START+timedelta(minutes=15*(i+1)),v) for i in range(4)])
 def test_curtailment_is_retained_not_screened(self):
  row=self.rows()[0];row['decrementalbidid']='Reported';self.assertEqual(p.interval(row)[2],[])

if __name__=='__main__':unittest.main()
