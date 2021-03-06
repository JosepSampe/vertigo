package com.urv.storlet.scanhtml;

import java.io.*;

import org.openstack.storlet.common.*;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

public class TesterDriver {
	
	public static final String INPUT_FILE_NAME = "input/index.html";
	public static final String OUTPUT_FILE_NAME = "input/index.html.filtered";
	public static final String OUTPUT_MD_FILE_NAME = "input/output_md.txt";
	public static final String LOGGER_FILE_NAME = "input/logger";	
	
	public static void main(String[] args) {
		System.out.println("entering main");
		try {

			FileInputStream infile = new FileInputStream(INPUT_FILE_NAME);
			FileOutputStream outfile = new FileOutputStream(OUTPUT_FILE_NAME);
			FileOutputStream outfile_md = new FileOutputStream(OUTPUT_MD_FILE_NAME);

			HashMap<String, String> md = new HashMap<String, String>();
			StorletInputStream inputStream1 = new StorletInputStream(infile.getFD(), md);
	        StorletObjectOutputStream outStream = new StorletObjectOutputStream(outfile.getFD(), md, outfile_md.getFD());
	        
	        ArrayList<StorletInputStream> inputStreams = new ArrayList<StorletInputStream>();
	        inputStreams.add(inputStream1);	        
	        ArrayList<StorletOutputStream> outStreams = new ArrayList<StorletOutputStream>();
	        outStreams.add(outStream);
	        
			FileOutputStream loggerFile = new FileOutputStream(LOGGER_FILE_NAME);	
			ScanHtml storlet = new ScanHtml();	
			StorletLogger logger = new StorletLogger(loggerFile.getFD());				
			Map<String, String> parameters = new HashMap<String, String>();	

			parameters.put("search","img");
			System.out.println("before storlet");
			storlet.invoke(inputStreams, outStreams, parameters, logger);
			System.out.println("after storlet");
			
			infile.close();
			outfile.close();
			outfile_md.close();
			
			loggerFile.close();
			
		}
		catch (Exception e) {
			System.out.println("had an exception");
			System.out.println(e.getMessage());
		}
		System.out.println("exiting main");
	}

}

